"""Provider-neutral candidates, actions and serialization."""
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = '1.0'
ACTIONS = ('KEEP', 'DELETE', 'SHORTEN_PAUSE')
CANDIDATE_TYPES = ('pause', 'filler_speech', 'repeated_phrase', 'false_start',
                   'self_correction', 'abandoned_sentence', 'cross_clip_repetition', 'long_hesitation')


@dataclass
class Candidate:
    candidate_id: str
    candidate_type: str
    ranges: list[dict]
    original_text: str
    context: list[str]
    evidence: dict
    allowed_actions: list[str] = field(default_factory=lambda: ['KEEP', 'DELETE'])
    group_id: str = ''

    def to_dict(self):
        return asdict(self)


def duration(ranges):
    return round(sum(r['source_end'] - r['source_start'] for r in ranges), 6)


def action_for(candidate, action='KEEP', confidence=None, reason='', decision_source='deterministic', target=None):
    return {'action_id': '', 'candidate_id': candidate.candidate_id, 'action': action,
            'candidate_type': candidate.candidate_type, 'confidence': confidence, 'reason': reason,
            'ranges': [dict(r) for r in candidate.ranges], 'original_text': candidate.original_text,
            'original_duration': duration(candidate.ranges), 'target_duration': target,
            'decision_source': decision_source, 'suppressed_by': None}
