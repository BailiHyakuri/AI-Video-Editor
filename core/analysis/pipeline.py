import hashlib
import json
import logging
from pathlib import Path
from core.analysis.candidates import detect_candidates
from core.analysis.decisions import build_decisions
from core.analysis.overlap import resolve_overlaps
from core.analysis.transcript import read_transcript
from core.edit_plan.report import render_report
from core.edit_plan.schema import SCHEMA_VERSION
from core.edit_plan.validation import validate_actions
from core.errors import PipelineError
from core.pipeline import portable_source_reference

log = logging.getLogger(__name__)


def analyze(transcript_path, settings, provider=None):
    settings.validate()
    data = read_transcript(transcript_path)
    log.info('transcript_validation')
    candidates = detect_candidates(data, settings)
    log.info('candidate_detection_complete: %d', len(candidates), extra={'event': 'candidate_detection_complete'})
    actions = resolve_overlaps(build_decisions(candidates, settings, provider))
    for index, action in enumerate(actions, 1):
        action['action_id'] = f'edit_{index:03d}'
    validate_actions(actions, candidates, data)
    log.info('edit_decision_validation_complete')
    counts = {key: sum(a['action'] == value for a in actions) for key, value in
              [('delete_actions', 'DELETE'), ('shorten_pause_actions', 'SHORTEN_PAUSE'), ('keep_actions', 'KEEP')]}
    removed = sum(a['original_duration'] if a['action'] == 'DELETE' else
                  a['original_duration'] - a['target_duration'] if a['action'] == 'SHORTEN_PAUSE' else 0 for a in actions)
    return {'schema_version': SCHEMA_VERSION, 'source_transcript_schema_version': data['schema_version'],
            'project': {key: data['project'][key] for key in ('name', 'clip_count', 'duration')}, 'dry_run': True,
            'analysis_mode': 'llm' if settings.llm_enabled else 'deterministic', 'edit_preset': settings.mode,
            'provider': {'name': settings.provider, 'model': settings.model} if settings.llm_enabled else None,
            'policy': {**settings.policy, 'pause_thresholds': list(settings.pause_thresholds)},
            'candidates': [c.to_dict() for c in candidates], 'actions': actions,
            'summary': {'actions_total': len(actions), **counts, 'estimated_removed_duration': round(removed, 6)}}


def analyze_to_directory(transcript_path, settings, output_dir=None, provider=None):
    transcript_path = Path(transcript_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve() if output_dir else transcript_path.parent / 'edit-analysis'
    if output.exists():
        raise PipelineError(f'Analysis output already exists: {output}. Choose a new --output-dir.')
    if not transcript_path.is_file():
        raise PipelineError(f'Project transcript does not exist: {transcript_path}')
    # CLI configures structured console logging; no transcript text or keys are logged.
    before = transcript_path.read_bytes()
    try:
        plan = analyze(transcript_path, settings, provider)
        if transcript_path.read_bytes() != before:
            raise PipelineError('Source transcript changed during analysis; retry with a stable input.')
        plan['source_transcript'] = {**portable_source_reference(transcript_path, output),
                                     'sha256': hashlib.sha256(before).hexdigest()}
        # Serialize both artifacts before creating the output directory.
        text = json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        report = render_report(plan)
        output.mkdir(parents=True, exist_ok=False)
        (output / 'edit_plan.json').write_text(text, encoding='utf-8')
        (output / 'edit_plan.txt').write_text(report, encoding='utf-8')
        log.info('edit_plan_output_written')
        return output, plan
    except Exception:
        log.error('edit_analysis_failed', extra={'event': 'edit_analysis_failed'})
        raise
