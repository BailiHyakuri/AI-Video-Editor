"""Phase 2 configuration is independent of ASR settings and contains no keys."""
import json
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit
from core.errors import PipelineError
from core.transcript import number

PRESETS = json.loads(Path(__file__).with_name('edit_presets.json').read_text(encoding='utf-8'))


@dataclass
class AnalysisSettings:
    mode: str = 'normal'
    llm_enabled: bool = False
    provider: str = 'openai'
    model: str | None = None
    timeout: float = 60
    retries: int = 2
    base_url: str | None = None
    pause_thresholds: tuple = (0.35, 0.65, 1.2, 2.0)
    target_pause_duration: float | None = None
    shorten_pause_min: float | None = None
    batch_size: int = 20
    context_chars: int = 600

    def validate(self):
        if not isinstance(self.mode, str) or self.mode not in PRESETS:
            raise PipelineError('Edit mode must be conservative, normal or aggressive.')
        if type(self.llm_enabled) is not bool:
            raise PipelineError('llm_enabled must be a boolean.')
        if not isinstance(self.provider, str) or self.provider not in ('openai', 'openai-compatible'):
            raise PipelineError('Provider must be openai or openai-compatible.')
        if self.model is not None and (not isinstance(self.model, str) or not self.model.strip()):
            raise PipelineError('Model must be a nonempty string or null.')
        if self.llm_enabled and not self.model:
            raise PipelineError('LLM analysis requires an explicitly configured --model.')
        number(self.timeout, 'LLM timeout')
        if self.timeout <= 0:
            raise PipelineError('LLM timeout must be positive.')
        for name, minimum, maximum in [('retries', 0, 10), ('batch_size', 1, 100), ('context_chars', 100, 10000)]:
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise PipelineError(f'{name} must be an integer between {minimum} and {maximum}.')
        if not isinstance(self.pause_thresholds, (list, tuple)) or len(self.pause_thresholds) != 4:
            raise PipelineError('pause_thresholds must contain four increasing positive numbers.')
        previous = 0
        for value in self.pause_thresholds:
            number(value, 'pause threshold')
            if value <= previous:
                raise PipelineError('Pause thresholds must be positive and strictly increasing.')
            previous = value
        for name in ('target_pause_duration', 'shorten_pause_min'):
            value = getattr(self, name)
            if value is not None:
                number(value, name)
                if value <= 0:
                    raise PipelineError(f'{name} must be positive.')
        if self.policy['target_pause_duration'] >= self.policy['shorten_pause_min']:
            raise PipelineError('Target pause duration must be smaller than the shortening threshold.')
        if self.base_url is not None:
            if not isinstance(self.base_url, str):
                raise PipelineError('base_url must be a URL string.')
            try:
                url = urlsplit(self.base_url)
                _ = url.port
            except ValueError as exc:
                raise PipelineError('Invalid base_url host or port.') from exc
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise PipelineError('base_url must be HTTP(S) with no credentials, query or fragment.')
            if self.provider != 'openai-compatible':
                raise PipelineError('Custom base URLs require the openai-compatible provider.')
        if self.llm_enabled and self.provider == 'openai-compatible' and not self.base_url:
            raise PipelineError('OpenAI-compatible analysis requires --base-url.')
        return self

    @property
    def policy(self):
        values = dict(PRESETS[self.mode])
        for name in ('target_pause_duration', 'shorten_pause_min'):
            if getattr(self, name) is not None:
                values[name] = getattr(self, name)
        return values

    @classmethod
    def load(cls, path=None):
        if path is None:
            return cls()
        try:
            data = json.loads(Path(path).expanduser().read_text(encoding='utf-8'))
            if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
                raise ValueError('Unknown settings; API keys must only be supplied through environment variables')
            return cls(**data)  # CLI overrides are applied before validation.
        except (OSError, ValueError, TypeError) as exc:
            raise PipelineError(f'Cannot read Phase 2 configuration {path}: {exc}') from exc
