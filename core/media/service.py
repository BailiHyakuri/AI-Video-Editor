import json
import math
from pathlib import Path
from core.errors import PipelineError
from core.tools import ProcessRunner, discover

SUPPORTED_SUFFIXES = {'.mov', '.mp4'}


def validate_input(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise PipelineError(f'Input does not exist or is not a file: {path}')
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise PipelineError(f'Unsupported input format: {path.suffix}. Supported: .mov, .mp4')
    return path


def parse_probe(data):
    streams = data.get('streams', [])
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    video = [s for s in streams if s.get('codec_type') == 'video']
    if not audio:
        raise PipelineError('Media has no audio stream.')
    if not video:
        raise PipelineError('Media has no video stream.')
    try:
        duration = float(data['format']['duration'])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise PipelineError('ffprobe did not provide a valid positive media duration.') from exc
    container = data.get('format', {}).get('format_name')
    if not isinstance(container, str) or not {'mov', 'mp4'} & set(container.split(',')):
        raise PipelineError(f'Unsupported media container: {container}. Expected MOV/MP4.')
    return {'duration': duration, 'container': container,
            'audio_streams': audio, 'video_streams': video}


class MediaService:
    def __init__(self, ffmpeg=None, ffprobe=None, runner=None, timeout=3600):
        self.ffmpeg = discover('ffmpeg', ffmpeg)
        self.ffprobe = discover('ffprobe', ffprobe)
        self.runner = runner or ProcessRunner()
        self.timeout = timeout

    def probe(self, path):
        result = self.runner.run([self.ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path], self.timeout)
        try:
            raw = getattr(result, 'stdout_bytes', None)
            if isinstance(raw, bytes):
                try:
                    stdout = raw.decode('utf-8')
                except UnicodeDecodeError as exc:
                    raise PipelineError(f'ffprobe subprocess stdout for {path} failed UTF-8 decoding at byte {exc.start}.') from exc
            else:
                stdout = result.stdout
            return parse_probe(json.loads(stdout))
        except (ValueError, TypeError, AttributeError) as exc:
            raise PipelineError(f'Invalid ffprobe response for {path}: {exc}') from exc

    def extract_audio(self, source, destination, duration):
        self.runner.run([self.ffmpeg, '-nostdin', '-v', 'error', '-y', '-copyts', '-start_at_zero', '-i', source,
                         '-map', '0:a:0', '-vn', '-af', 'aresample=16000:async=1:first_pts=0,apad',
                         '-t', str(duration), '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', destination], self.timeout)
