"""Use measured token/word bounds; never interpolate character timestamps."""
from core.analysis.transcript import source_range


def precise_units(segment):
    for level in ('words', 'tokens'):
        units = segment.get(level) or []
        if not units or any(u['start'] is None or u['end'] is None or u['end'] <= u['start'] for u in units):
            continue
        # Accept either raw concatenation (tokens/CJK) or word-separated text.
        for separator in ('', ' '):
            combined = separator.join(u['text'] for u in units)
            if combined.strip() == segment['text'].strip():
                return units, level[:-1], separator, len(combined) - len(combined.lstrip())
    return None


def span_ranges(segment, clip, start=0, end=None):
    end = len(segment['text']) if end is None else end
    if start == 0 and end == len(segment['text']):
        if segment['source_end'] <= segment['source_start']:
            return [], False
        return [source_range(clip, segment['source_start'], segment['source_end'], 'segment')], True
    precise = precise_units(segment)
    if precise:
        units, level, separator, leading = precise
        spans = []
        cursor = -leading
        for unit in units:
            spans.append((cursor, cursor + len(unit['text']), unit))
            cursor += len(unit['text']) + len(separator)
        selected = [(left, right, unit) for left, right, unit in spans if right > start and left < end]
        # Trimming surrounding whitespace changes no spoken-unit boundary.
        if selected:
            left, right = selected[0][0], selected[-1][1]
            if segment['text'][max(left, 0):start].strip() == '' and segment['text'][end:right].strip() == '':
                first, last = selected[0][2], selected[-1][2]
                if last['end'] > first['start'] and all(a[2]['end'] <= b[2]['start'] for a, b in zip(selected, selected[1:])):
                    return [source_range(clip, first['start'], last['end'], level)], True
    # A partial phrase without exact boundaries is review-only, never whole-span DELETE.
    ranges, _ = span_ranges(segment, clip)
    return ranges, False
