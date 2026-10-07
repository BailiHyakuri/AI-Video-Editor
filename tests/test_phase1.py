import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.asr.base import TranscriptionOptions
from core.asr.whisper_cpp import WhisperCppProvider, normalize
from core.cli import main
from core.config.settings import Settings
from core.errors import PipelineError
from core.media.service import MediaService, parse_probe, validate_input
from core.pipeline import project_inputs, transcribe_project
from core.tools import ProcessRunner, discover
from core.transcript import NormalizedTranscript, Segment, TimedText


class FakeMedia:
    def probe(self, path):
        return {'duration': 10.0, 'container': 'mov', 'audio_streams': [], 'video_streams': []}

    def extract_audio(self, source, destination, duration):
        destination.write_bytes(b'fake')


class FakeProvider:
    def __init__(self, fail=False):
        self.paths = []
        self.fail = fail

    def transcribe(self, audio_path, options):
        self.paths.append(audio_path)
        if self.fail:
            raise PipelineError('Transcription failed')
        return NormalizedTranscript('zh', '你好', [Segment('你好', 1.0, 2.5)])


class Phase1Tests(unittest.TestCase):
    def test_schema_validation(self):
        for start, end in [(-1, 2), (3, 2), (float('nan'), 2), (0, float('inf'))]:
            with self.assertRaises(PipelineError):
                NormalizedTranscript(None, '', [Segment('a', start, end)]).validate()
        with self.assertRaises(PipelineError):
            TimedText('a', None, 1).validate(optional=True)
        with self.assertRaises(PipelineError):
            TimedText('a', 0, 1, 1.1).validate()
        with self.assertRaises(PipelineError):
            NormalizedTranscript('en', 'a', [Segment('a', 0, 20)]).validate(10)
        self.assertIsNone(TimedText('a', None, None).confidence)

    def test_whisper_normalization(self):
        transcript = normalize({'result': {'language': 'zh'}, 'transcription': [
            {'text': ' 你好', 'offsets': {'from': 1000, 'to': 2500}, 'tokens': [
                {'text': '你', 'offsets': {'from': 1000, 'to': 1200}, 'p': .8},
                {'text': '好'}, {'text': '[_EOT_]', 'p': .9}]}]})
        self.assertEqual(transcript.language, 'zh')
        self.assertEqual(transcript.segments[0].start, 1)
        self.assertEqual(transcript.segments[0].tokens[0].confidence, .8)
        self.assertIsNone(transcript.segments[0].tokens[1].start)
        self.assertIsNone(transcript.segments[0].words)
        with self.assertRaises(PipelineError):
            normalize({})
        self.assertEqual(normalize({'transcription': []}).text, '')

    def test_probe(self):
        info = parse_probe({'format': {'duration': '10.25', 'format_name': 'mov,mp4'}, 'streams': [
            {'codec_type': 'audio', 'codec_name': 'aac', 'sample_rate': '48000'},
            {'codec_type': 'video', 'codec_name': 'h264', 'avg_frame_rate': '30000/1001'}]})
        self.assertEqual(info['duration'], 10.25)
        self.assertEqual(info['audio_streams'][0]['sample_rate'], '48000')
        self.assertEqual(info['video_streams'][0]['avg_frame_rate'], '30000/1001')
        for data in [{}, {'streams': [{'codec_type': 'audio'}]},
                     {'format': {'duration': 'NaN'}, 'streams': [{'codec_type': 'audio'}, {'codec_type': 'video'}]}]:
            with self.assertRaises(PipelineError):
                parse_probe(data)

    def test_paths_and_directory_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / '项目 with spaces'
            clips = root / 'clips'
            clips.mkdir(parents=True)
            for name in ['002.mp4', '001.MOV']:
                (clips / name).touch()
            self.assertEqual([p.name for p in project_inputs(root)], ['001.MOV', '002.mp4'])
            self.assertEqual(validate_input(clips / '001.MOV'), (clips / '001.MOV').resolve())
            (clips / 'bad.txt').touch()
            with self.assertRaises(PipelineError):
                validate_input(clips / 'bad.txt')
            with self.assertRaises(PipelineError):
                validate_input(clips / 'missing.mp4')

    def test_order_offsets_outputs_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = [root / 'second.mp4', root / 'first.mov']
            for path in inputs:
                path.touch()
            provider = FakeProvider()
            settings = Settings(output_dir=str(root / 'output'), temporary_dir=str(root / 'temp'))
            output = transcribe_project(inputs, 'demo', settings, FakeMedia(), provider)
            data = json.loads((output / 'project_transcript.json').read_text())
            self.assertEqual(data['project']['duration'], 20)
            self.assertEqual(data['clips'][0]['original_filename'], 'second.mp4')
            self.assertEqual(data['segments'][1]['source_start'], 1)
            self.assertEqual(data['segments'][1]['project_start'], 11)
            self.assertEqual(data['segments'][1]['project_end'], 12.5)
            self.assertEqual(data['segments'][1]['clip_id'], 'clip_002')
            self.assertEqual(data['languages'], ['zh'])
            self.assertTrue((output / 'clips/clip_002/transcript.txt').is_file())
            self.assertTrue((output / 'logs/pipeline.jsonl').is_file())
            for path in provider.paths:
                self.assertFalse(path.exists())
            with self.assertRaises(PipelineError):
                transcribe_project(inputs, 'demo', settings, FakeMedia(), provider)
            output2 = transcribe_project(inputs, 'demo2', settings, FakeMedia(), FakeProvider())
            other = json.loads((output2 / 'project_transcript.json').read_text())
            other['project']['name'] = 'demo'
            self.assertEqual(data, other)

    def test_failure_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'video.mov'
            source.touch()
            provider = FakeProvider(fail=True)
            with self.assertRaises(PipelineError):
                transcribe_project([source], 'failure', Settings(output_dir=str(root / 'out')), FakeMedia(), provider)
            self.assertFalse(provider.paths[0].exists())
            self.assertTrue(source.exists())
            self.assertFalse((root / 'out/failure/project_transcript.json').exists())
            self.assertIn('pipeline_error', (root / 'out/failure/logs/pipeline.jsonl').read_text())

    def test_provider_contract_and_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model, audio = root / 'model.bin', root / 'clip.wav'
            model.touch(); audio.touch()
            class Runner:
                def run(self, args, timeout):
                    self.args = args
                    self.timeout = timeout
                    prefix = Path(args[args.index('-of') + 1])
                    prefix.with_suffix('.json').write_text('{"transcription": []}')
            runner = Runner()
            with patch('core.asr.whisper_cpp.discover', return_value=root / 'whisper-cli'):
                provider = WhisperCppProvider(model, runner=runner)
            self.assertIsInstance(provider.transcribe(audio, TranscriptionOptions('zh', 12)), NormalizedTranscript)
            self.assertIn('-ojf', runner.args)
            self.assertEqual(runner.timeout, 12)
            with self.assertRaises(PipelineError):
                WhisperCppProvider(root / 'missing.bin')

    def test_audio_extraction_arguments(self):
        runner = SimpleNamespace(run=lambda args, timeout: setattr(self, 'args', args))
        with patch('core.media.service.discover', side_effect=lambda name, path: Path(name)):
            service = MediaService(runner=runner)
        service.extract_audio(Path('a b.mov'), Path('out.wav'), 10)
        self.assertIn('pcm_s16le', self.args)
        self.assertIn('16000', self.args)
        self.assertIn('0:a:0', self.args)

    def test_process_errors(self):
        with patch('core.tools.shutil.which', return_value=None):
            with self.assertRaises(PipelineError):
                discover('ffmpeg')
        for error in [subprocess.TimeoutExpired('tool', 1), OSError('missing')]:
            with patch('core.tools.subprocess.run', side_effect=error):
                with self.assertRaises(PipelineError):
                    ProcessRunner().run(['tool'], 1)
        with patch('core.tools.subprocess.run', return_value=SimpleNamespace(returncode=2, stderr='bad input')):
            with self.assertRaisesRegex(PipelineError, 'bad input'):
                ProcessRunner().run(['tool'])

    def test_configuration_and_cli(self):
        for settings in [Settings(timeout=-1), Settings(timeout=float('nan')), Settings(log_level='bad')]:
            with self.assertRaises(PipelineError):
                settings.validate()
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(['transcribe', 'nonexistent.mov']), 1)
        self.assertIn('does not exist', error.getvalue())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            path.write_text('{"unknown": 2}')
            with self.assertRaises(PipelineError):
                Settings.load(path)


if __name__ == '__main__':
    unittest.main()
