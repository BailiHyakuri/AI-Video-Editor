from core.analysis.overlap import intersects
from core.analysis.transcript import EPSILON, close, require
from core.edit_plan.schema import ACTIONS, CANDIDATE_TYPES, duration
from core.errors import PipelineError
from core.transcript import number


def validate_actions(actions, candidates, transcript):
    clip_map = {clip['clip_id']: clip for clip in transcript['clips']}
    candidate_map = {c.candidate_id: c for c in candidates}
    boundaries = {clip_id: {0, clip['source_duration']} for clip_id, clip in clip_map.items()}
    for segment in transcript['segments']:
        known = boundaries[segment['clip_id']]
        known.update((segment['source_start'], segment['source_end']))
        for units in (segment.get('words'), segment.get('tokens')):
            for unit in units or []:
                if unit['start'] is not None:
                    known.update((unit['start'], unit['end']))
    require(len(actions) == len(candidates), 'Plan must preserve every candidate decision.')
    seen, seen_actions = set(), set()
    try:
        for action in actions:
            candidate = candidate_map.get(action['candidate_id'])
            require(candidate is not None and action['candidate_id'] not in seen, 'Unknown/duplicate plan candidate.')
            seen.add(action['candidate_id'])
            require(isinstance(action['action_id'], str) and action['action_id'] and action['action_id'] not in seen_actions,
                    'Action IDs must be unique nonempty strings.')
            seen_actions.add(action['action_id'])
            require(action['action'] in ACTIONS and action['candidate_type'] in CANDIDATE_TYPES, 'Unknown action/candidate type.')
            require(action['candidate_type'] == candidate.candidate_type and action['action'] in candidate.allowed_actions,
                    'Action does not match its supplied candidate.')
            require(action['ranges'] == candidate.ranges and action['original_text'] == candidate.original_text,
                    'Action ranges/text must come from its candidate, never LLM timestamps.')
            require(action['ranges'], 'Action needs at least one source range.')
            require(action['decision_source'] in ('deterministic', 'llm', 'hybrid'), 'Invalid decision source.')
            require(isinstance(action['reason'], str) and action['reason'], 'Action needs a reason.')
            if action['confidence'] is not None:
                number(action['confidence'], 'action confidence')
                require(0 <= action['confidence'] <= 1, 'Invalid confidence.')
            previous_end = -1
            for r in action['ranges']:
                clip = clip_map.get(r['clip_id'])
                require(clip is not None, 'Action refers to an unknown clip.')
                for key in ('source_start', 'source_end', 'project_start', 'project_end'):
                    number(r[key], key)
                require(0 <= r['source_start'] < r['source_end'] <= clip['source_duration'],
                        'Action source range is negative, empty or out of bounds.')
                require(all(any(close(r[key], value) for value in boundaries[r['clip_id']])
                            for key in ('source_start', 'source_end')), 'Action endpoints are not measured transcript/clip boundaries.')
                require(0 <= r['project_start'] < r['project_end'] <= transcript['project']['duration'] + EPSILON,
                        'Action project range is negative, empty or out of bounds.')
                require(close(r['project_start'], clip['project_start'] + r['source_start']) and
                        close(r['project_end'], clip['project_start'] + r['source_end']), 'Action timeline mismatch.')
                require(r['project_start'] >= previous_end, 'Action ranges must be ordered and nonoverlapping.')
                previous_end = r['project_end']
                require(r['timing_level'] in ('segment', 'word', 'token', 'gap'), 'Invalid timing provenance.')
            require(close(action['original_duration'], duration(action['ranges'])), 'Action duration mismatch.')
            if action['action'] == 'SHORTEN_PAUSE':
                require(action['candidate_type'] in ('pause', 'long_hesitation'), 'Only pause candidates may be shortened.')
                number(action['target_duration'], 'target duration')
                require(0 < action['target_duration'] < action['original_duration'], 'Invalid shortened pause duration.')
            else:
                require(action['target_duration'] is None, 'Non-pause action cannot specify target duration.')
            if action['suppressed_by'] is not None:
                require(action['action'] == 'KEEP' and action['suppressed_by'] in candidate_map,
                        'Invalid overlap suppression reference.')
        active = [a for a in actions if a['action'] != 'KEEP']
        for i, action in enumerate(active):
            require(not any(intersects(action, other) for other in active[:i]), 'Conflicting non-KEEP actions overlap.')
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise PipelineError(f'Malformed edit action: {exc}') from exc
    return actions
