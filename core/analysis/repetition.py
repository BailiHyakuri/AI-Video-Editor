import re
import unicodedata
from difflib import SequenceMatcher
from core.analysis.timing import span_ranges
from core.edit_plan.schema import Candidate


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def clauses(segment):
    # Keep delimiters in the deletion span: boundaries still require transcript timing.
    result = []
    for match in re.finditer(r'[^，,。.!！?？;；…]+[，,。.!！?？;；…]*', segment['text']):
        start, end = match.span()
        while start < end and segment['text'][start].isspace():
            start += 1
        if normalized(segment['text'][start:end]):
            result.append((start, end, segment['text'][start:end]))
    return result


def detect_repetition(data, settings):
    clips = {c['clip_id']: c for c in data['clips']}
    all_units = [(segment_index, segment, start, end, text) for segment_index, segment in enumerate(data['segments'])
                 for start, end, text in clauses(segment)]
    units = [(index, *unit) for index, unit in enumerate(all_units)
             if len(normalized(unit[-1])) >= settings.policy['exact_repeat_min_chars']]
    candidates = []
    for first, second in zip(units, units[1:]):
        first_index, segment_index, segment, start, end, text = first
        second_index, next_index, following, _, _, next_text = second
        if next_index - segment_index > 1:
            continue
        a, b = normalized(text), normalized(next_text)
        if min(len(a), len(b)) < settings.policy['exact_repeat_min_chars']:
            continue
        relation = 'exact' if a == b else 'prefix' if b.startswith(a) else 'near'
        ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
        if relation == 'near' and ratio < settings.policy['near_repeat_ratio']:
            continue
        ranges, precise = span_ranges(segment, clips[segment['clip_id']], start, end)
        if not ranges:
            continue
        cross_clip = segment['clip_id'] != following['clip_id']
        candidates.append(Candidate('', 'cross_clip_repetition' if cross_clip else 'repeated_phrase',
                                    ranges, text, [segment['text'], following['text']],
                                    {'relation': relation, 'similarity': round(ratio, 6), 'precise': precise,
                                     'directly_adjacent': second_index == first_index + 1,
                                     'verbatim': unicodedata.normalize('NFKC', text).strip() == unicodedata.normalize('NFKC', next_text).strip(),
                                     'segment_refs': [f"{segment['clip_id']}:{segment['id']}",
                                                      f"{following['clip_id']}:{following['id']}"]},
                                    ['KEEP', 'DELETE'] if precise else ['KEEP']))
    return candidates
