import math
from dataclasses import dataclass, field, asdict
from core.errors import PipelineError

SCHEMA_VERSION = '1.1'


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PipelineError(f'Invalid {label}: expected a finite number.')


@dataclass
class TimedText:
    text: str
    start: float | None
    end: float | None
    confidence: float | None = None

    def validate(self, optional=False):
        if not isinstance(self.text, str):
            raise PipelineError('Transcript text must be a string.')
        if not (optional and self.start is None and self.end is None):
            number(self.start, 'start'); number(self.end, 'end')
            if self.start < 0 or self.end < self.start:
                raise PipelineError('Transcript timestamps must be nonnegative and ordered.')
        if self.confidence is not None:
            number(self.confidence, 'confidence')
            if not 0 <= self.confidence <= 1:
                raise PipelineError('Confidence must be between zero and one.')


@dataclass
class Token(TimedText):
    # Present only when upstream byte-fragment tokens need lossless reassembly.
    components: list[dict] | None = None

    def validate(self, optional=False):
        super().validate(optional)
        for part in self.components or []:
            TimedText('', part.get('start'), part.get('end'), part.get('confidence')).validate(optional=True)


@dataclass
class Segment(TimedText):
    id: int = 0
    words: list[TimedText] | None = None
    tokens: list[TimedText] | None = None


@dataclass
class NormalizedTranscript:
    language: str | None
    text: str
    segments: list[Segment] = field(default_factory=list)

    def validate(self, duration=None):
        if self.language is not None and not isinstance(self.language, str):
            raise PipelineError('Transcript language must be a string or null.')
        if not isinstance(self.text, str):
            raise PipelineError('Transcript text must be a string.')
        previous = 0
        for index, segment in enumerate(self.segments):
            segment.validate()
            if segment.id != index or segment.start < previous:
                raise PipelineError('Segment IDs must be sequential and timestamps ordered.')
            previous = segment.start
            if duration is not None and segment.end > duration + 0.1:
                raise PipelineError('ASR segment exceeds source duration.')
            for items in (segment.words, segment.tokens):
                for item in items or []:
                    item.validate(optional=True)
                    if item.start is not None and (item.start < segment.start or item.end > segment.end + 0.1):
                        raise PipelineError('Word/token timing exceeds its segment.')
        return self

    def to_dict(self):
        self.validate()
        return asdict(self)


def assemble_project(name, clips):
    offset = 0.0
    segments, ordered, languages = [], [], []
    for order, clip in enumerate(clips, 1):
        duration = clip['source']['duration']
        number(duration, 'source duration')
        if duration <= 0:
            raise PipelineError('Source duration must be positive.')
        ordered.append({'clip_id': clip['clip_id'], 'source_file': clip['source']['file'],
                        'source_path_base': clip['source']['path_base'],
                        'original_filename': clip['source']['original_filename'], 'order': order,
                        'source_duration': duration, 'project_start': round(offset, 6),
                        'project_end': round(offset + duration, 6)})
        if clip['language'] and clip['language'] not in languages:
            languages.append(clip['language'])
        for segment in clip['segments']:
            mapped = {**segment, 'clip_id': clip['clip_id'], 'source_file': clip['source']['file'],
                      'source_path_base': clip['source']['path_base']}
            mapped['source_start'] = mapped.pop('start')
            mapped['source_end'] = mapped.pop('end')
            mapped['project_start'] = round(offset + mapped['source_start'], 6)
            mapped['project_end'] = round(offset + mapped['source_end'], 6)
            segments.append(mapped)
        offset += duration
    return {'schema_version': SCHEMA_VERSION, 'project': {'name': name, 'clip_count': len(clips), 'duration': round(offset, 6)},
            'languages': languages, 'clips': ordered, 'text': '\n'.join(c['text'] for c in clips if c['text']), 'segments': segments}
