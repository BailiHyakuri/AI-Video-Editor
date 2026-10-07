import json
import logging
import os
import re
import tempfile
from pathlib import Path
from core.asr.base import ASRProvider, TranscriptionOptions
from core.errors import PipelineError
from core.logging_utils import JsonFormatter
from core.media.service import validate_input
from core.transcript import SCHEMA_VERSION, assemble_project

log = logging.getLogger('core.pipeline')


def project_inputs(directory):
    directory = Path(directory).expanduser().resolve()
    clips = directory / 'clips'
    if not clips.is_dir():
        raise PipelineError(f'Project needs a clips directory: {clips}')
    # Every regular file is validated, so unsupported files are not silently skipped.
    files = sorted((p for p in clips.iterdir() if p.is_file() and not p.name.startswith('.')), key=lambda p: p.name)
    if not files:
        raise PipelineError(f'No clips found in {clips}')
    return files


def portable_source_reference(source, project_directory):
    """Paths are relative to the output project, never the per-clip JSON folder.

    Unrelated filesystem roots/drives cannot have a useful portable link. Those
    sources are relative to their input parent, rebound by clip ID after moving.
    """
    try:
        common = Path(os.path.commonpath([source.parent, project_directory]))
        if common == Path(common.anchor):
            return {'file': source.name, 'path_base': 'input_root'}
        relative = Path(os.path.relpath(source, project_directory)).as_posix()
        return {'file': relative, 'path_base': 'project'}
    except ValueError:  # Windows paths on different drives.
        return {'file': source.name, 'path_base': 'input_root'}


def write_outputs(directory, stem, data):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{stem}.json').write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (directory / f'{stem}.txt').write_text(data['text'] + '\n', encoding='utf-8')


def transcribe_project(inputs, name, settings, media, provider: ASRProvider, progress=None):
    settings.validate()
    if not re.fullmatch(r'[\w.-]+', name) or name in ('.', '..'):
        raise PipelineError('Project name must contain only letters, digits, underscores, dots or hyphens.')
    if not inputs:
        raise PipelineError('At least one input clip is required.')
    paths = [validate_input(p) for p in inputs]
    output = Path(settings.output_dir).expanduser().resolve() / name
    # Exclusive creation protects prior transcript identities and user files.
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise PipelineError(f'Output already exists: {output}. Use a different --name or --output-dir.') from exc
    (output / 'logs').mkdir()
    handler = logging.FileHandler(output / 'logs' / 'pipeline.jsonl', encoding='utf-8')
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger('core')
    old_level = logger.level
    logger.setLevel(settings.log_level.upper())
    logger.addHandler(handler)
    try:
        metadata = []
        for path in paths:
            log.info('input_validation', extra={'source_file': str(path)})
            log.info('media_probe', extra={'source_file': str(path)})
            metadata.append(media.probe(path))
        temporary_dir = Path(settings.temporary_dir).expanduser().resolve() if settings.temporary_dir else None
        if temporary_dir:
            temporary_dir.mkdir(parents=True, exist_ok=True)
        clips = []
        with tempfile.TemporaryDirectory(prefix='ai-video-asr-', dir=temporary_dir) as workspace:
            for order, (path, info) in enumerate(zip(paths, metadata), 1):
                clip_id = f'clip_{order:03d}'
                if progress:
                    progress(f'[{order}/{len(paths)}] Transcribing {path.name} ({clip_id})')
                details = {'clip_id': clip_id, 'source_file': str(path)}
                audio = Path(workspace) / f'{clip_id}.wav'
                log.info('audio_extraction', extra=details)
                media.extract_audio(path, audio, info['duration'])
                log.info('asr_start', extra=details)
                transcript = provider.transcribe(audio, TranscriptionOptions(settings.language, settings.timeout))
                transcript.validate(info['duration'])
                log.info('asr_completion', extra=details)
                clip = {'schema_version': SCHEMA_VERSION, 'clip_id': clip_id, 'order': order,
                        'source': {**portable_source_reference(path, output), 'original_filename': path.name,
                                   'duration': info['duration'], 'metadata': info}, **transcript.to_dict()}
                clips.append(clip)
                write_outputs(output / 'clips' / clip_id, 'transcript', clip)
                log.info('output_writing', extra=details)
        project = assemble_project(name, clips)
        write_outputs(output, 'project_transcript', project)
        log.info('project_completion')
        return output
    except Exception as exc:
        log.error('pipeline_error: %s', exc, extra={'event': 'pipeline_error'})
        # Retain completed clips and logs; absence of project_transcript signals failure.
        raise
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(old_level)
