"""Human review only; no video execution or shell command generation."""


def timestamp(seconds):
    centiseconds = round(seconds * 100)
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, fraction = divmod(remainder, 100)
    prefix = f'{hours:02d}:' if hours else ''
    return f'{prefix}{minutes:02d}:{seconds:02d}.{fraction:02d}'


def render_report(plan):
    summary = plan['summary']
    lines = [f"DRY RUN — {plan['project']['name']}", 'No video has been changed.',
             f"Mode: {plan['edit_preset']} / {plan['analysis_mode']}",
             f"Candidates: {summary['actions_total']}; DELETE: {summary['delete_actions']}; "
             f"SHORTEN_PAUSE: {summary['shorten_pause_actions']}; KEEP: {summary['keep_actions']}",
             f"Estimated removed duration: {summary['estimated_removed_duration']:.2f} sec", '']
    candidates = {c['candidate_id']: c for c in plan['candidates']}
    omitted = 0
    for action in plan['actions']:
        candidate = candidates[action['candidate_id']]
        if action['action'] == 'KEEP' and action['candidate_type'] == 'pause' and candidate['evidence'].get('category') in ('short', 'medium'):
            omitted += 1
            continue
        lines.append(f"{action['action']} — {action['action_id']} / {action['candidate_type']}")
        for r in action['ranges']:
            lines.append(f"  Project {timestamp(r['project_start'])} → {timestamp(r['project_end'])}; "
                         f"{r['clip_id']} source {timestamp(r['source_start'])} → {timestamp(r['source_end'])} ({r['timing_level']})")
        if action['action'] == 'SHORTEN_PAUSE':
            lines.append(f"  Original: {action['original_duration']:.2f} sec; target: {action['target_duration']:.2f} sec")
        lines.append('  Reason: ' + action['reason'])
        if action['original_text']:
            lines.append('  Text: ' + action['original_text'])
        lines.append('')
    if omitted:
        lines.append(f'{omitted} preserved short/medium pause candidates omitted; all decisions are in edit_plan.json.')
    return '\n'.join(lines) + '\n'
