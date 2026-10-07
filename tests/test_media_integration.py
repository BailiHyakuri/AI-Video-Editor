"""Small synthetic-media smoke test; no user media or Whisper model required."""
import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from core.config.settings import Settings
from core.media.service import MediaService
from core.pipeline import transcribe_project
from core.tools import ProcessRunner, discover
from core.transcript import NormalizedTranscript, Segment


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg/ffprobe not installed')
class MediaIntegrationTests(unittest.TestCase):
    def test_delayed_audio_and_real_media_pipeline(self):
        with tempfile.TemporaryDirectory(prefix='phase1-smoke-') as temp:
            root = Path(temp)
            source = root / 'delayed audio.mp4'
            ProcessRunner().run([discover('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                'color=c=black:s=64x64:r=10:d=3', '-itsoffset', '1', '-f', 'lavfi', '-i',
                'sine=frequency=440:sample_rate=48000:duration=2', '-c:v', 'mpeg4',
                '-c:a', 'aac', '-t', '3', source], timeout=30)
            case = self

            class FakeASR:
                def transcribe(self, audio_path, options):
                    with wave.open(str(audio_path)) as audio:
                        case.assertEqual(audio.getframerate(), 16000)
                        case.assertEqual(audio.getnchannels(), 1)
                        case.assertEqual(audio.getsampwidth(), 2)
                        case.assertAlmostEqual(audio.getnframes() / 16000, 3, places=2)
                        case.assertFalse(any(audio.readframes(8000)), 'Leading source silence must remain')
                    return NormalizedTranscript('en', 'Synthetic test', [Segment('Synthetic test', 1, 2)])

            output = transcribe_project([source, source], 'smoke', Settings(output_dir=str(root / 'out')),
                                        MediaService(timeout=30), FakeASR())
            project = json.loads((output / 'project_transcript.json').read_text())
            self.assertEqual(project['segments'][1]['source_start'], 1)
            self.assertEqual(project['segments'][1]['project_start'], 4)
            self.assertEqual(project['project']['duration'], 6)
