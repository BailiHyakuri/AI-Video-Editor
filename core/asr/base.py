from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from core.transcript import NormalizedTranscript


@dataclass(frozen=True)
class TranscriptionOptions:
    language: str = 'auto'
    timeout: float = 3600


class ASRProvider(Protocol):
    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> NormalizedTranscript:
        """Return source-relative seconds without source or project assumptions."""
        ...
