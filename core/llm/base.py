"""Vendor-neutral semantic review. Responses never contain paths or timestamps."""
from dataclasses import dataclass
from typing import Protocol

DECISION_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['decisions'], 'properties': {'decisions': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['candidate_id', 'action', 'confidence', 'reason'],
        'properties': {'candidate_id': {'type': 'string'},
                       'action': {'type': 'string', 'enum': ['KEEP', 'DELETE', 'SHORTEN_PAUSE']},
                       'confidence': {'type': 'number'}, 'reason': {'type': 'string'}}}}}}


@dataclass(frozen=True)
class StructuredRequest:
    instructions: str
    payload: dict
    schema: dict


class LLMProvider(Protocol):
    def generate_structured(self, request: StructuredRequest) -> dict:
        """Return candidate choices only; no provider-specific objects in core."""
        ...
