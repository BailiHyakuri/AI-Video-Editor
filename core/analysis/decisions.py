import logging
from pathlib import Path
from core.errors import PipelineError
from core.edit_plan.schema import action_for, duration
from core.llm.base import DECISION_SCHEMA, StructuredRequest
from core.transcript import number

PROMPT = Path(__file__).resolve().parents[2] / 'prompts' / 'candidate_review.md'
PAUSE_TYPES = ('pause', 'long_hesitation')


def deterministic_decision(candidate, settings):
    evidence = candidate.evidence
    if candidate.candidate_type in PAUSE_TYPES:
        if evidence.get('has_speech') and duration(candidate.ranges) >= settings.policy['shorten_pause_min']:
            return action_for(candidate, 'SHORTEN_PAUSE', 1.0,
                              'Transcript gap exceeds preset threshold; proposed shortening requires human review.',
                              target=settings.policy['target_pause_duration'])
        return action_for(candidate, reason='Preserve short/natural pauses or an empty transcript; gap is not proof of silence.')
    if candidate.candidate_type in ('repeated_phrase', 'cross_clip_repetition') and evidence.get('relation') == 'exact' and evidence.get('directly_adjacent') and evidence.get('verbatim') and 'DELETE' in candidate.allowed_actions:
        return action_for(candidate, 'DELETE', 1.0, 'Adjacent verbatim repetition; retain the later occurrence. Review possible intentional emphasis.')
    reason = 'Lexical/structural hint only; semantic intent is uncertain without LLM or human review.'
    if candidate.allowed_actions == ['KEEP']:
        reason = 'Review-only phrase: exact word/token boundaries are unavailable; preserve surrounding content.'
    return action_for(candidate, reason=reason)


def request_for(candidates, settings):
    payload = []
    for c in candidates:
        texts = [c.original_text, *c.context]
        truncated = any(len(text) > settings.context_chars for text in texts)
        payload.append({'candidate_id': c.candidate_id, 'group_id': c.group_id,
                        'candidate_type': c.candidate_type,
                        'original_text': c.original_text[:settings.context_chars],
                        'context': [text[:settings.context_chars] for text in c.context],
                        'evidence': {k: v for k, v in c.evidence.items() if k != 'segment_refs'},
                        'duration': duration(c.ranges), 'truncated': truncated,
                        'allowed_actions': ['KEEP'] if truncated else c.allowed_actions})
    try:
        instructions = PROMPT.read_text(encoding='utf-8')
    except OSError as exc:
        raise PipelineError(f'Cannot read semantic review prompt: {PROMPT}') from exc
    return StructuredRequest(instructions, {'mode': settings.mode, 'policy': settings.policy, 'candidates': payload}, DECISION_SCHEMA)


def validate_choices(response, request):
    if not isinstance(response, dict) or set(response) != {'decisions'} or not isinstance(response['decisions'], list):
        raise PipelineError('Malformed LLM decisions: expected only a decisions array.')
    supplied = {c['candidate_id']: c for c in request.payload['candidates']}
    result = {}
    for choice in response['decisions']:
        if not isinstance(choice, dict) or set(choice) != {'candidate_id', 'action', 'confidence', 'reason'}:
            raise PipelineError('Malformed LLM decision fields: timestamps, paths and extra fields are forbidden.')
        candidate_id = choice['candidate_id']
        if not isinstance(candidate_id, str) or candidate_id not in supplied or candidate_id in result:
            raise PipelineError('LLM returned unknown or duplicate candidate ID.')
        if choice['action'] not in supplied[candidate_id]['allowed_actions']:
            raise PipelineError('LLM chose an action not allowed for this candidate.')
        number(choice['confidence'], 'LLM confidence')
        if not 0 <= choice['confidence'] <= 1 or not isinstance(choice['reason'], str) or not choice['reason'].strip() or len(choice['reason']) > 2000:
            raise PipelineError('LLM confidence/reason is invalid.')
        try:
            choice['reason'].encode('utf-8')
        except UnicodeEncodeError as exc:
            raise PipelineError('LLM reason contains invalid Unicode; no plan was saved.') from exc
        result[candidate_id] = choice
    if set(result) != set(supplied):
        raise PipelineError('LLM omitted candidate decisions; no plan was saved.')
    return result


def build_decisions(candidates, settings, provider=None):
    if not settings.llm_enabled:
        return [deterministic_decision(c, settings) for c in candidates]
    if provider is None:
        raise PipelineError('LLM analysis was enabled without a provider.')
    choices = {}
    # Semantic groups are kept together whenever they fit; large groups retain
    # bounded per-candidate context when split, avoiding unbounded API requests.
    grouped = {}
    for c in candidates:
        grouped.setdefault(c.group_id, []).append(c)
    batches, pending = [], []
    for group in grouped.values():
        if len(pending) + len(group) > settings.batch_size and pending:
            batches.append(pending)
            pending = []
        if len(group) > settings.batch_size:
            batches.extend(group[start:start + settings.batch_size] for start in range(0, len(group), settings.batch_size))
        else:
            pending.extend(group)
    if pending:
        batches.append(pending)
    for batch in batches:
        logging.getLogger(__name__).info('llm_candidate_review_start: %d', len(batch), extra={'event': 'llm_candidate_review_start'})
        request = request_for(batch, settings)
        choices.update(validate_choices(provider.generate_structured(request), request))
        logging.getLogger(__name__).info('llm_candidate_review_complete', extra={'event': 'llm_candidate_review_complete'})
    actions = []
    for c in candidates:
        choice = choices[c.candidate_id]
        action, reason = choice['action'], choice['reason']
        source = 'llm'
        if action != 'KEEP' and choice['confidence'] < settings.policy['delete_min_confidence']:
            source = 'hybrid'
            action, reason = 'KEEP', 'Confidence below preset threshold; preserved. ' + reason
        if c.candidate_type in PAUSE_TYPES and action != 'KEEP' and duration(c.ranges) < settings.policy['shorten_pause_min']:
            source = 'hybrid'
            action, reason = 'KEEP', 'Gap is below the preset shortening threshold; preserved. ' + reason
        target = settings.policy['target_pause_duration'] if action == 'SHORTEN_PAUSE' else None
        if target is not None and target >= duration(c.ranges):
            source = 'hybrid'
            action, target, reason = 'KEEP', None, 'Configured target would not shorten this gap; preserved. ' + reason
        actions.append(action_for(c, action, choice['confidence'], reason, source, target))
    return actions
