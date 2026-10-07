"""Regression for whisper.cpp byte-level BPE tokens in full JSON output."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.asr.base import TranscriptionOptions
from core.asr.whisper_cpp import WhisperCppProvider, normalize, read_generated_json
from core.errors import PipelineError
from core.media.service import MediaService
from core.tools import ProcessRunner

TEXT = '中文剪辑 English 日本語テスト'


def broken_full_json():
    # Mirror v1.8.2: a UTF-8 character is split into raw-byte JSON token strings.
    tokens = []
    pieces = []
    for char in TEXT:
        encoded = char.encode('utf-8')
        pieces.extend([encoded[:2], encoded[2:]] if len(encoded) > 2 else [encoded])
    for index, piece in enumerate(pieces):
        tokens.append({'text': f'PIECE_{index}', 'id': index, 'p': .75,
                       'offsets': {'from': 0, 'to': 1000}})
    data = {'result': {'language': 'zh'}, 'transcription': [
        {'text': TEXT, 'offsets': {'from': 0, 'to': 1000}, 'tokens': tokens}]}
    raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
    for index, piece in enumerate(pieces):
        raw = raw.replace(f'"PIECE_{index}"'.encode(), b'"' + piece + b'"')
    return raw, len(pieces)


class EncodingTests(unittest.TestCase):
    def test_lossless_multilingual_fragment_reassembly(self):
        raw, count = broken_full_json()
        with self.assertRaises(UnicodeDecodeError):
            raw.decode('utf-8')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'clip_001-asr.json'
            path.write_bytes(raw)
            transcript = normalize(read_generated_json(path))
            self.assertEqual(transcript.text, TEXT)
            tokens = transcript.segments[0].tokens
            self.assertEqual(''.join(t.text for t in tokens), TEXT)
            self.assertEqual(path.read_bytes(), raw, 'Reader must not modify diagnostic source')
            probabilities = []
            for token in tokens:
                if getattr(token, 'components', None):
                    self.assertIsNone(token.confidence, 'Do not invent a merged probability')
                    probabilities.extend(c['confidence'] for c in token.components)
                    self.assertEqual(bytes.fromhex(''.join(c['text_bytes_hex'] for c in token.components)).decode('utf-8'), token.text)
                else:
                    probabilities.append(token.confidence)
            self.assertEqual(probabilities, [.75] * count)
            json.dumps(transcript.to_dict(), ensure_ascii=False).encode('utf-8', errors='strict')

    def test_lone_surrogate_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'escaped.json'
            path.write_bytes(b'{"transcription": [{"text": "\\ud800"}]}')
            with self.assertRaisesRegex(PipelineError, 'Generated transcript JSON.*UTF-8'):
                read_generated_json(path)

    def test_valid_utf8_unchanged(self):
        data = {'result': {'language': 'ja'}, 'transcription': [
            {'text': TEXT, 'offsets': {'from': 0, 'to': 1000}, 'tokens': [{'text': TEXT, 'p': .9}]}]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'valid.json'
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(read_generated_json(path), data)

    def test_invalid_segment_bytes_rejected_with_file_and_encoding(self):
        raw, _ = broken_full_json()
        raw = raw.replace(TEXT.encode('utf-8'), b'bad\xff text', 1)
        self.assert_decode_rejected(raw)

    def test_unrecoverable_token_bytes_rejected(self):
        self.assert_decode_rejected(b'{"transcription":[{"tokens":[{"text":"\xff"}]}]}')

    def test_incomplete_token_not_joined_across_segments(self):
        self.assert_decode_rejected(b'{"transcription":[{"tokens":[{"text":"\xe5\x89"}]},'
                                   b'{"tokens":[{"text":"\xaa"}]}]}')

    def assert_decode_rejected(self, raw):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'clip_002-asr.json'
            path.write_bytes(raw)
            with self.assertRaises(PipelineError) as caught:
                read_generated_json(path)
            message = str(caught.exception)
            self.assertIn(str(path), message)
            self.assertIn('Generated transcript JSON', message)
            self.assertIn('UTF-8', message)
            self.assertIn('bytes', message)

    def test_console_bytes_are_distinct_from_generated_transcript(self):
        raw, _ = broken_full_json()
        console = (TEXT.encode('utf-8') + b'\xff', b'engine diagnostic\xfe')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio, model = root / 'clip.wav', root / 'model.bin'
            audio.touch(); model.touch()
            audio.with_name('clip-asr.json').write_bytes(raw)
            completed = subprocess.CompletedProcess(['whisper-cli'], 0, *console)
            with patch('core.tools.subprocess.run', return_value=completed), \
                 patch('core.asr.whisper_cpp.discover', return_value=Path('whisper-cli')), \
                 self.assertLogs('core.tools', level='WARNING') as logs:
                provider = WhisperCppProvider(model, runner=ProcessRunner())
                transcript = provider.transcribe(audio, TranscriptionOptions())
            self.assertEqual(transcript.text, TEXT)
            self.assertEqual(completed.stdout_bytes, console[0])
            self.assertEqual(completed.stderr_bytes, console[1])
            self.assertIn('\\xff', completed.stdout)
            self.assertIn('\\xfe', completed.stderr)
            self.assertIn('subprocess stdout', logs.output[0])
            self.assertIn('subprocess stderr', logs.output[1])
            self.assertIn('UTF-8', logs.output[0])

    def test_ffprobe_structured_stdout_is_strict_utf8(self):
        completed = subprocess.CompletedProcess(['ffprobe'], 0, b'{"format":"\xff"}', b'')
        with patch('core.media.service.discover', side_effect=lambda name, path: Path(name)), \
             patch('core.tools.subprocess.run', return_value=completed), \
             self.assertLogs('core.tools', level='WARNING'):
            media = MediaService()
            with self.assertRaisesRegex(PipelineError, 'ffprobe subprocess stdout.*UTF-8'):
                media.probe(Path('video.mov'))
