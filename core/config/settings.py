import json
import math
from dataclasses import dataclass, fields
from pathlib import Path
from core.errors import PipelineError


@dataclass
class Settings:
    output_dir: str = 'Output'
    temporary_dir: str | None = None
    whisper_binary: str | None = None
    model: str | None = None
    ffmpeg: str | None = None
    ffprobe: str | None = None
    language: str = 'auto'
    log_level: str = 'INFO'
    timeout: float = 3600

    def validate(self):
        for key in ('output_dir', 'language', 'log_level'):
            if not isinstance(getattr(self, key), str) or not getattr(self, key):
                raise PipelineError(f'Configuration {key} must be a nonempty string.')
        for key in ('temporary_dir', 'whisper_binary', 'model', 'ffmpeg', 'ffprobe'):
            if getattr(self, key) is not None and not isinstance(getattr(self, key), str):
                raise PipelineError(f'Configuration {key} must be a path string or null.')
        if self.log_level.upper() not in ('DEBUG', 'INFO', 'WARNING', 'ERROR'):
            raise PipelineError('log_level must be DEBUG, INFO, WARNING, or ERROR.')
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise PipelineError('Timeout must be a finite positive number of seconds.')
        return self

    @classmethod
    def load(cls, path=None):
        if not path:
            return cls()
        try:
            data = json.loads(Path(path).expanduser().read_text(encoding='utf-8'))
            if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
                raise ValueError('Unknown configuration keys or invalid object')
            return cls(**data).validate()
        except (OSError, ValueError, TypeError) as exc:
            raise PipelineError(f'Cannot load configuration: {exc}') from exc
