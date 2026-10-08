"""Transcript gaps are candidates, not proof of acoustic silence."""
from core.analysis.timing import precise_units
from core.analysis.transcript import project_ranges
from core.edit_plan.schema import Candidate


def pause_category(seconds, thresholds):
    small, medium, long, very_long = thresholds
    if seconds < small:
        return None
    if seconds < medium:
        return 'short'
    if seconds < long:
        return 'medium'
    if seconds <= very_long:
        return 'long'
    return 'very_long'


def detect_pauses(data, settings):
    coverage = []
    clips = {c['clip_id']: c for c in data['clips']}
    for segment in data['segments']:
        precise = precise_units(segment)
        # Without complete trustworthy unit timing, retain full segment coverage.
        units = precise[0] if precise else [{'start': segment['source_start'], 'end': segment['source_end']}]
        offset = clips[segment['clip_id']]['project_start']
        coverage.extend((round(offset + u['start'], 6), round(offset + u['end'], 6)) for u in units if u['end'] > u['start'])
    merged = []
    for start, end in sorted(coverage):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    gaps = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < data['project']['duration']:
        gaps.append((cursor, data['project']['duration']))
    candidates = []
    for start, end in gaps:
        seconds = round(end - start, 6)
        category = pause_category(seconds, settings.pause_thresholds)
        if not category:
            continue
        context = [s['text'] for s in data['segments'] if abs(s['project_end'] - start) < 1e-6 or abs(s['project_start'] - end) < 1e-6]
        if not context:
            nearby = sorted(data['segments'], key=lambda s: min(abs(s['project_start'] - end), abs(s['project_end'] - start)))
            context = [s['text'] for s in nearby[:2]]
        candidates.append(Candidate('', 'pause', project_ranges(data, start, end), '', context,
                                    {'category': category, 'duration': seconds, 'has_speech': bool(merged),
                                     'basis': 'transcript_gap', 'segment_refs': []},
                                    ['KEEP', 'SHORTEN_PAUSE', 'DELETE'] if merged else ['KEEP']))
        if category == 'very_long' and any(text.strip().startswith(('嗯', '呃', '额', '那个', '怎么说呢')) for text in context):
            candidates.append(Candidate('', 'long_hesitation', project_ranges(data, start, end), '', context,
                                        {'category': category, 'duration': seconds, 'has_speech': bool(merged),
                                         'basis': 'gap_adjacent_to_hesitation_marker', 'segment_refs': []},
                                        ['KEEP', 'SHORTEN_PAUSE', 'DELETE']))
    return candidates
