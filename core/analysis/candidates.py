"""Conservative lexical hints; marker matches alone never imply deletion."""
import re
from core.analysis.pauses import detect_pauses
from core.analysis.repetition import detect_repetition, normalized
from core.analysis.timing import span_ranges
from core.edit_plan.schema import Candidate

CORRECTION = re.compile(r'(?:不是[，,\s]*)?(?:不对[，,\s]*)?我重新说|不对|应该说|我说错了')
FILLER = re.compile(r'(?:^|[，,。.!！?？;；\s])(?P<filler>嗯+|呃+|额+|那个|怎么说呢)(?=$|[，,。.!！?？;；\s])')
UNFINISHED = re.compile(r'(?:…+|\.{3,}|—+|-{2,})\s*$')


def detect_speech(data):
    clips = {c['clip_id']: c for c in data['clips']}
    candidates = []
    segments = data['segments']
    for index, segment in enumerate(segments):
        clip = clips[segment['clip_id']]
        context = [s['text'] for s in segments[max(0, index - 1):index + 2]]
        ref = f"{segment['clip_id']}:{segment['id']}"
        for match in FILLER.finditer(segment['text']):
            start, end = match.span('filler')
            ranges, precise = span_ranges(segment, clip, start, end)
            if ranges:
                candidates.append(Candidate('', 'filler_speech', ranges, match.group('filler'), context,
                                            {'precise': precise, 'basis': 'standalone_marker_needs_semantic_review',
                                             'segment_refs': [ref]}, ['KEEP', 'DELETE'] if precise else ['KEEP']))
        correction = CORRECTION.search(segment['text'])
        if correction:
            # Include abandoned text before marker, never corrected text after it.
            end = correction.end()
            while end < len(segment['text']) and segment['text'][end] in ',， 。':
                end += 1
            marker_only = not normalized(segment['text'][end:]) and not normalized(segment['text'][:correction.start()])
            ranges, precise = span_ranges(segment, clip, 0, end)
            original = segment['text'][:end]
            refs = [ref]
            if marker_only and index > 0:
                prior = segments[index - 1]
                earlier, earlier_precise = span_ranges(prior, clips[prior['clip_id']])
                ranges = earlier + ranges
                precise = precise and earlier_precise
                original = prior['text'] + '\n' + original
                refs.insert(0, f"{prior['clip_id']}:{prior['id']}")
            if ranges:
                candidates.append(Candidate('', 'self_correction', ranges, original, context,
                                            {'precise': precise, 'marker': correction.group(), 'segment_refs': refs},
                                            ['KEEP', 'DELETE'] if precise else ['KEEP']))
        if UNFINISHED.search(segment['text']):
            ranges, precise = span_ranges(segment, clip)
            if not ranges:
                continue
            following = segments[index + 1] if index + 1 < len(segments) else None
            prefix = normalized(segment['text'])
            restart = bool(following and len(prefix) >= 3 and normalized(following['text']).startswith(prefix))
            candidates.append(Candidate('', 'false_start' if restart else 'abandoned_sentence', ranges,
                                        segment['text'], context, {'precise': precise, 'segment_refs': [ref],
                                        'basis': 'unfinished_text_and_restart' if restart else 'unfinished_text_hint'}))
    return candidates


def group_candidates(candidates):
    # Group semantic alternatives sharing selected speech segments. Pause intervals
    # are grouped by overlap, without linking every gap to all neighboring speech.
    parents = list(range(len(candidates)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    for i, candidate in enumerate(candidates):
        refs = set(candidate.evidence.get('segment_refs', []))
        for j in range(i):
            other = candidates[j]
            overlaps = any(a['clip_id'] == b['clip_id'] and max(a['source_start'], b['source_start']) < min(a['source_end'], b['source_end'])
                           for a in candidate.ranges for b in other.ranges)
            if refs.intersection(other.evidence.get('segment_refs', [])) or overlaps:
                parents[root(i)] = root(j)
    groups = {}
    for i, candidate in enumerate(candidates):
        key = root(i)
        if key not in groups:
            groups[key] = f'group_{len(groups) + 1:03d}'
        candidate.group_id = groups[key]
    return candidates


def detect_candidates(data, settings):
    candidates = detect_pauses(data, settings) + detect_repetition(data, settings) + detect_speech(data)
    candidates.sort(key=lambda c: (c.ranges[0]['project_start'], c.candidate_type, c.ranges[-1]['project_end']))
    for index, candidate in enumerate(candidates, 1):
        candidate.candidate_id = f'candidate_{index:03d}'
    return group_candidates(candidates)
