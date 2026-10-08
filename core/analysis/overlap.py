"""Containment first, then semantic priority; losers become reviewable KEEP."""
PRIORITY = {'self_correction': 0, 'false_start': 1, 'abandoned_sentence': 2,
            'cross_clip_repetition': 3, 'repeated_phrase': 4, 'filler_speech': 5,
            'long_hesitation': 6, 'pause': 7}


def intersects(a, b):
    return any(x['clip_id'] == y['clip_id'] and max(x['source_start'], y['source_start']) < min(x['source_end'], y['source_end'])
               for x in a['ranges'] for y in b['ranges'])


def contains(a, b):
    for item in b['ranges']:
        # A containing union can have touching ranges within one clip.
        coverage = sorted((r['source_start'], r['source_end']) for r in a['ranges'] if r['clip_id'] == item['clip_id'])
        cursor = item['source_start']
        for start, end in coverage:
            if start <= cursor <= end:
                cursor = max(cursor, end)
        if cursor < item['source_end']:
            return False
    return True


def resolve_overlaps(actions):
    active = [a for a in actions if a['action'] != 'KEEP']
    # Rank a containing DELETE ahead of lower-level actions, including pause edits.
    ranked = sorted(active, key=lambda a: (
        -sum(a['action'] == 'DELETE' and contains(a, b) and not contains(b, a) for b in active if b is not a),
        PRIORITY[a['candidate_type']], -a['original_duration'], a['candidate_id']))
    accepted = []
    for action in ranked:
        conflict = next((other for other in accepted if intersects(action, other)), None)
        if conflict:
            if action['decision_source'] == 'llm':
                action['decision_source'] = 'hybrid'
            action['action'] = 'KEEP'
            action['target_duration'] = None
            action['confidence'] = None
            action['suppressed_by'] = conflict['candidate_id']
            action['reason'] = 'Preserved to avoid overlap with ' + conflict['candidate_id'] + '. ' + action['reason']
        else:
            accepted.append(action)
    return actions
