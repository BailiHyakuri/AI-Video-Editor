"""Read-only Phase 1 adapter and strict source/project timeline validation."""
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from core.errors import PipelineError
from core.transcript import TimedText, number

EPSILON = 0.000001


def require(condition, message):
    if not condition:
        raise PipelineError(message)


def close(a, b):
    return abs(a - b) <= EPSILON


def validate_transcript(data):
    try:
        require(isinstance(data, dict), 'Transcript must be a JSON object.')
        require(data['schema_version'] in ('1.0', '1.1'), 'Unsupported Phase 1 transcript schema version.')
        project, clips, segments = data['project'], data['clips'], data['segments']
        require(isinstance(project, dict) and isinstance(project['name'], str), 'Invalid project identification.')
        require(isinstance(clips, list) and clips, 'Project needs an ordered nonempty clip list.')
        require(isinstance(segments, list) and isinstance(data['text'], str), 'Invalid transcript segments/text.')
        require(type(project['clip_count']) is int and project['clip_count'] == len(clips), 'Clip count mismatch.')
        number(project['duration'], 'project duration')
        clip_map = {}
        offset = 0.0
        for order, clip in enumerate(clips, 1):
            clip_id = clip['clip_id']
            require(isinstance(clip_id, str) and clip_id and clip_id not in clip_map, 'Clip IDs must be unique strings.')
            require(type(clip['order']) is int and clip['order'] == order, 'Clip list order mismatch.')
            require(isinstance(clip['source_file'], str) and clip['source_file'], 'Invalid clip source reference.')
            require(isinstance(clip['original_filename'], str), 'Missing original filename.')
            if data['schema_version'] == '1.1':
                require(clip['source_path_base'] in ('project', 'input_root'), 'Invalid source path base.')
                require(not PurePosixPath(clip['source_file']).is_absolute() and not PureWindowsPath(clip['source_file']).anchor
                        and '\\' not in clip['source_file'], 'Schema 1.1 source references must be portable relative POSIX paths.')
            for key in ('source_duration', 'project_start', 'project_end'):
                number(clip[key], key)
            require(clip['source_duration'] > 0 and close(clip['project_start'], offset) and
                    close(clip['project_end'], offset + clip['source_duration']), 'Invalid clip timeline mapping.')
            clip_map[clip_id] = clip
            offset += clip['source_duration']
        require(close(project['duration'], offset), 'Project duration mismatch.')
        counts = dict.fromkeys(clip_map, 0)
        previous = -1.0
        for segment in segments:
            clip = clip_map.get(segment['clip_id'])
            require(clip is not None, 'Segment refers to an unknown clip.')
            require(type(segment['id']) is int and segment['id'] == counts[segment['clip_id']], 'Segment IDs must be ordered within clips.')
            counts[segment['clip_id']] += 1
            for key in ('source_start', 'source_end', 'project_start', 'project_end'):
                number(segment[key], key)
            require(0 <= segment['source_start'] <= segment['source_end'] <= clip['source_duration'] + EPSILON,
                    'Segment outside source duration.')
            require(close(segment['project_start'], clip['project_start'] + segment['source_start']) and
                    close(segment['project_end'], clip['project_start'] + segment['source_end']), 'Segment timeline mismatch.')
            require(segment['project_start'] >= previous, 'Segments must follow project order.')
            previous = segment['project_start']
            require(segment['source_file'] == clip['source_file'], 'Segment source reference mismatch.')
            if data['schema_version'] == '1.1':
                require(segment['source_path_base'] == clip['source_path_base'], 'Segment path base mismatch.')
            TimedText(segment['text'], segment['source_start'], segment['source_end'], segment.get('confidence')).validate()
            for level in ('words', 'tokens'):
                units = segment.get(level)
                require(units is None or isinstance(units, list), f'{level} must be a list or null.')
                for unit in units or []:
                    TimedText(unit['text'], unit['start'], unit['end'], unit.get('confidence')).validate(optional=True)
                    if unit['start'] is not None:
                        require(unit['start'] >= segment['source_start'] and unit['end'] <= segment['source_end'] + EPSILON,
                                f'{level} timing outside segment.')
        require(isinstance(data.get('languages'), list) and all(isinstance(v, str) for v in data['languages']), 'Invalid languages.')
        return data
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise PipelineError(f'Malformed Phase 1 project transcript: {exc}') from exc


def read_transcript(path):
    try:
        return validate_transcript(json.loads(Path(path).read_text(encoding='utf-8')))
    except (OSError, ValueError) as exc:
        raise PipelineError(f'Cannot read project transcript {path} as UTF-8 JSON: {exc}') from exc


def source_range(clip, start, end, level):
    return {'clip_id': clip['clip_id'], 'source_start': start, 'source_end': end,
            'project_start': round(clip['project_start'] + start, 6),
            'project_end': round(clip['project_start'] + end, 6), 'timing_level': level}


def project_ranges(data, start, end, level='gap'):
    ranges = []
    for clip in data['clips']:
        left, right = max(start, clip['project_start']), min(end, clip['project_end'])
        if right > left:
            ranges.append(source_range(clip, round(left - clip['project_start'], 6),
                                       round(right - clip['project_start'], 6), level))
    return ranges
