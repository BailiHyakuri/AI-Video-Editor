import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.analysis.candidates import detect_candidates
from core.analysis.decisions import build_decisions, request_for, validate_choices
from core.analysis.overlap import resolve_overlaps
from core.analysis.pauses import pause_category
from core.analysis.pipeline import analyze, analyze_to_directory
from core.analysis.transcript import validate_transcript
from core.cli import main
from core.config.analysis_settings import AnalysisSettings
from core.edit_plan.schema import Candidate, action_for
from core.edit_plan.validation import validate_actions
from core.errors import PipelineError
from core.transcript import NormalizedTranscript, Segment, TimedText, assemble_project


def project(*clip_segments, durations=None):
    clips = []
    durations = durations or [max((s.end for s in segments), default=5) for segments in clip_segments]
    for order, (segments, duration) in enumerate(zip(clip_segments, durations), 1):
        for index, segment in enumerate(segments):
            segment.id = index
        transcript = NormalizedTranscript('zh', '\n'.join(s.text for s in segments), segments)
        clips.append({'clip_id': f'clip_{order:03d}', 'source': {'file': f'clips/{order:03d}.mov',
                      'path_base': 'project', 'original_filename': f'{order:03d}.mov', 'duration': duration},
                      **transcript.to_dict()})
    return assemble_project('test-project', clips)


class FakeLLM:
    def __init__(self, action='KEEP', confidence=.99, choose_type=None):
        self.requests = []
        self.action, self.confidence, self.choose_type = action, confidence, choose_type

    def generate_structured(self, request):
        self.requests.append(request)
        return {'decisions': [{'candidate_id': c['candidate_id'],
                              'action': self.action if self.choose_type is None or c['candidate_type'] == self.choose_type else 'KEEP',
                              'confidence': self.confidence, 'reason': 'Mock semantic review.'}
                             for c in request.payload['candidates']]}


class AnalysisTests(unittest.TestCase):
    def run_plan(self, data, settings=None, provider=None):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'project_transcript.json'
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            return analyze(path, settings or AnalysisSettings(), provider)

    def test_pause_categories(self):
        thresholds = (.35, .65, 1.2, 2)
        self.assertEqual([pause_category(t, thresholds) for t in (.349, .35, .649, .65, 1.199, 1.2, 2, 2.001)],
                         [None, 'short', 'short', 'medium', 'medium', 'long', 'long', 'very_long'])

    def test_pause_detection_and_preset_behavior(self):
        data = project([Segment('第一句话', 0, 1), Segment('第二句话', 2, 3)])
        for mode, expected in [('conservative', 'KEEP'), ('normal', 'KEEP'), ('aggressive', 'SHORTEN_PAUSE')]:
            plan = self.run_plan(data, AnalysisSettings(mode=mode))
            pause = next(a for a in plan['actions'] if a['candidate_type'] == 'pause')
            self.assertEqual(pause['action'], expected)
            if expected != 'KEEP':
                self.assertEqual(pause['target_duration'], .2)
        longer = project([Segment('第一句话', 0, 1), Segment('第二句话', 3, 4)])
        plan = self.run_plan(longer)
        self.assertEqual(plan['summary']['estimated_removed_duration'], 1.7)

    def test_cross_clip_gap_has_two_real_source_ranges(self):
        data = project([Segment('第一段', 0, 1)], [Segment('第二段', 1, 2)], durations=[3, 2])
        plan = self.run_plan(data)
        pause = next(a for a in plan['actions'] if a['action'] == 'SHORTEN_PAUSE')
        self.assertEqual([r['clip_id'] for r in pause['ranges']], ['clip_001', 'clip_002'])
        self.assertEqual([(r['source_start'], r['source_end']) for r in pause['ranges']], [(1, 3), (0, 1)])
        self.assertEqual(pause['ranges'][1]['project_start'], 3)
        self.assertEqual(pause['original_duration'], 3)

    def test_token_timing_without_words_and_missing_timing_fallback(self):
        data = project([Segment('AB', 0, 5, tokens=[TimedText('A', 0, 1), TimedText('B', 3, 5)])])
        plan = self.run_plan(data)
        self.assertEqual(plan['summary']['shorten_pause_actions'], 1)
        data['segments'][0]['tokens'][1]['start'] = None
        data['segments'][0]['tokens'][1]['end'] = None
        self.assertEqual(self.run_plan(data)['summary']['actions_total'], 0)

    def test_exact_repetition_and_near_repetition(self):
        data = project([Segment('我们以后会加入自动字幕。', 0, 2), Segment('我们以后会加入自动字幕。', 2, 4)])
        action = self.run_plan(data)['actions'][0]
        self.assertEqual(action['action'], 'DELETE')
        self.assertEqual(action['ranges'][0]['source_end'], 2)
        near = project([Segment('接下来我会讲这个软件的技术结构。', 0, 2),
                        Segment('接下来我来讲这个软件的技术结构。', 2, 4)])
        action = self.run_plan(near)['actions'][0]
        self.assertEqual(action['candidate_type'], 'repeated_phrase')
        self.assertEqual(action['action'], 'KEEP')

    def test_cross_clip_repetition_with_intro_word(self):
        data = project([Segment('接下来我会讲这个软件的技术结构。', 0, 2)],
                       [Segment('好，接下来我来讲这个软件的技术结构。', 0, 2)])
        candidates = detect_candidates(data, AnalysisSettings())
        candidate = next(c for c in candidates if c.candidate_type == 'cross_clip_repetition')
        self.assertEqual(candidate.ranges[0]['clip_id'], 'clip_001')
        self.assertEqual(self.run_plan(data)['actions'][0]['action'], 'KEEP')

    def test_filler_keyword_never_implies_deletion(self):
        data = project([Segment('问题就是出在这里。那个问题其实很重要。', 0, 3)])
        self.assertFalse(any(c.candidate_type == 'filler_speech' for c in detect_candidates(data, AnalysisSettings())))
        filler = project([Segment('嗯', 0, 1)])
        action = self.run_plan(filler)['actions'][0]
        self.assertEqual(action['candidate_type'], 'filler_speech')
        self.assertEqual(action['action'], 'KEEP')

    def test_partial_filler_without_timing_is_review_only(self):
        data = project([Segment('嗯，下一段正文', 0, 4)])
        candidate = next(c for c in detect_candidates(data, AnalysisSettings()) if c.candidate_type == 'filler_speech')
        self.assertEqual(candidate.allowed_actions, ['KEEP'])
        with self.assertRaises(PipelineError):
            build_decisions([candidate], AnalysisSettings(llm_enabled=True, model='test'), FakeLLM('DELETE'))

    def test_precise_partial_filler_uses_tokens(self):
        data = project([Segment('嗯，正文', 0, 4, tokens=[TimedText('嗯', 0, 1), TimedText('，', 1, 1.1), TimedText('正文', 1.1, 4)])])
        candidate = next(c for c in detect_candidates(data, AnalysisSettings()) if c.candidate_type == 'filler_speech')
        self.assertEqual(candidate.ranges[0]['timing_level'], 'token')
        self.assertEqual(candidate.ranges[0]['source_end'], 1)

    def test_correction_preserves_corrected_statement(self):
        data = project([Segment('所有模型都必须使用OpenAI', 0, 2), Segment('不对，我重新说', 2, 3),
                        Segment('核心不依赖任何单一AI Provider', 3, 5)])
        candidate = next(c for c in detect_candidates(data, AnalysisSettings()) if c.candidate_type == 'self_correction')
        self.assertEqual(candidate.ranges[-1]['source_end'], 3)
        self.assertIn('核心不依赖', candidate.context[-1])
        self.assertEqual(self.run_plan(data)['actions'][0]['action'], 'KEEP')
        plan = self.run_plan(data, AnalysisSettings(llm_enabled=True, model='test'), FakeLLM('DELETE', choose_type='self_correction'))
        action = next(a for a in plan['actions'] if a['candidate_type'] == 'self_correction')
        self.assertEqual(action['action'], 'DELETE')
        self.assertEqual(action['ranges'][-1]['source_end'], 3)

    def test_false_start_and_abandoned_sentence(self):
        data = project([Segment('我觉得这个……', 0, 1), Segment('我觉得这个问题其实很简单。', 1, 3),
                        Segment('下一件事……', 3, 4)])
        types = {c.candidate_type for c in detect_candidates(data, AnalysisSettings())}
        self.assertIn('false_start', types)
        self.assertIn('abandoned_sentence', types)
        self.assertTrue(all(a['action'] == 'KEEP' for a in self.run_plan(data)['actions']))

    def test_long_hesitation_candidate(self):
        data = project([Segment('嗯', 0, 1), Segment('现在继续', 4, 5)])
        self.assertIn('long_hesitation', {c.candidate_type for c in detect_candidates(data, AnalysisSettings())})
        plan = self.run_plan(data)
        self.assertEqual(plan['summary']['shorten_pause_actions'], 1)
        self.assertTrue(any(a['suppressed_by'] for a in plan['actions']))

    def test_overlap_containment_and_partial_priority(self):
        def item(identifier, kind, start, end, action):
            c = Candidate(identifier, kind, [{'clip_id': 'clip_001', 'source_start': start, 'source_end': end,
                          'project_start': start, 'project_end': end, 'timing_level': 'segment'}], '', [], {})
            return action_for(c, action, .99, 'test', target=.3 if action == 'SHORTEN_PAUSE' else None)
        outer = item('candidate_001', 'self_correction', 0, 5, 'DELETE')
        inner = item('candidate_002', 'pause', 1, 3, 'SHORTEN_PAUSE')
        resolved = resolve_overlaps([inner, outer])
        self.assertEqual(inner['action'], 'KEEP')
        self.assertEqual(inner['suppressed_by'], outer['candidate_id'])
        self.assertEqual(outer['action'], 'DELETE')
        a = item('candidate_003', 'filler_speech', 4, 6, 'DELETE')
        resolve_overlaps([outer, a])
        self.assertEqual(a['action'], 'KEEP')

    def test_plan_validation_rejects_invented_or_out_of_range_times(self):
        data = project([Segment('相同的一句话内容', 0, 2), Segment('相同的一句话内容', 2, 4)])
        candidates = detect_candidates(data, AnalysisSettings())
        action = action_for(candidates[0], 'DELETE', .98, 'test')
        action['action_id'] = 'edit_001'
        validate_actions([action], candidates, data)
        for key, value in [('clip_id', 'unknown'), ('source_start', -1), ('source_end', 99), ('project_start', 99)]:
            changed = copy.deepcopy(action)
            changed['ranges'][0][key] = value
            with self.assertRaises(PipelineError):
                validate_actions([changed], candidates, data)
        changed_candidates = copy.deepcopy(candidates)
        changed_candidates[0].ranges[0]['source_start'] = .123
        changed_candidates[0].ranges[0]['project_start'] = .123
        invented = action_for(changed_candidates[0], 'DELETE', .98, 'test')
        invented['action_id'] = 'edit_001'
        with self.assertRaisesRegex(PipelineError, 'measured'):
            validate_actions([invented], changed_candidates, data)

    def test_invalid_transcript_mapping_and_portable_paths(self):
        data = project([Segment('正文', 0, 2)])
        for mutation in [lambda d: d['segments'][0].update(project_end=99),
                         lambda d: d['clips'][0].update(source_file='/Users/machine/video.mov'),
                         lambda d: d['clips'][0].update(source_file='C:/private/video.mov'),
                         lambda d: d['project'].update(duration=float('nan')),
                         lambda d: d['segments'][0].update(clip_id='unknown')]:
            changed = copy.deepcopy(data)
            mutation(changed)
            with self.assertRaises(PipelineError):
                validate_transcript(changed)
        self.assertIs(validate_transcript(data), data)

    def test_llm_rejects_unknown_duplicate_missing_extra_and_nan(self):
        data = project([Segment('嗯', 0, 1)])
        candidates = detect_candidates(data, AnalysisSettings())
        settings = AnalysisSettings(llm_enabled=True, model='test')
        request = request_for(candidates, settings)
        good = FakeLLM().generate_structured(request)
        cases = [None, {}, {'decisions': []}]
        for changes in [{'candidate_id': 'invented'}, {'source_start': 0}, {'confidence': float('nan')}, {'confidence': '0.9'}, {'action': 'execute_shell'}]:
            changed = copy.deepcopy(good)
            changed['decisions'][0].update(changes)
            cases.append(changed)
        duplicate = copy.deepcopy(good)
        duplicate['decisions'] *= 2
        cases.append(duplicate)
        for response in cases:
            with self.assertRaises(PipelineError):
                validate_choices(response, request)

    def test_low_confidence_and_truncated_text_preserved(self):
        data = project([Segment('嗯', 0, 1)])
        plan = self.run_plan(data, AnalysisSettings(llm_enabled=True, model='test'), FakeLLM('DELETE', .2))
        self.assertEqual(plan['actions'][0]['action'], 'KEEP')
        data['segments'][0]['text'] = '嗯，' + '中文' * 100
        request = request_for(detect_candidates(data, AnalysisSettings()), AnalysisSettings(context_chars=100))
        self.assertTrue(request.payload['candidates'][0]['truncated'])
        self.assertEqual(request.payload['candidates'][0]['allowed_actions'], ['KEEP'])

    def test_multilingual_privacy_payload_and_provider_abstraction(self):
        data = project([Segment('中文 English 日本語テスト', 0, 2), Segment('中文 English 日本語テスト', 2, 4)])
        fake = FakeLLM()
        plan = self.run_plan(data, AnalysisSettings(llm_enabled=True, model='model-per-task'), fake)
        payload = json.dumps(fake.requests[0].payload, ensure_ascii=False)
        self.assertIn('日本語', payload)
        self.assertNotIn('source_file', payload)
        self.assertNotIn('project_start', payload)
        self.assertNotIn('source_start', payload)
        self.assertEqual(plan['provider']['model'], 'model-per-task')

    def test_no_ai_cli_output_and_unchanged_transcript(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'project_transcript.json'
            path.write_text(json.dumps(project([Segment('第一句话', 0, 1), Segment('第二句话', 4, 5)])))
            before = path.read_bytes()
            with patch('core.llm.openai_provider.create_client', side_effect=AssertionError('No API calls allowed')), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['analyze', str(path), '--no-llm']), 0)
            output = root / 'edit-analysis'
            self.assertTrue((output / 'edit_plan.txt').is_file())
            saved = json.loads((output / 'edit_plan.json').read_text())
            self.assertTrue(saved['dry_run'])
            self.assertIsNone(saved['provider'])
            self.assertEqual(saved['source_transcript']['file'], '../project_transcript.json')
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(PipelineError):
                analyze_to_directory(path, AnalysisSettings())

    def test_llm_pause_edit_respects_preset_threshold(self):
        data = project([Segment('前一句话', 0, 1), Segment('后一句话', 2, 3)])
        plan = self.run_plan(data, AnalysisSettings(llm_enabled=True, model='test'), FakeLLM('SHORTEN_PAUSE'))
        self.assertEqual(plan['actions'][0]['action'], 'KEEP')
        self.assertEqual(plan['actions'][0]['decision_source'], 'hybrid')

    def test_group_aware_bounded_batches(self):
        candidates = []
        for index in range(6):
            ranges = [{'clip_id': 'clip_001', 'source_start': index, 'source_end': index + 1,
                       'project_start': index, 'project_end': index + 1, 'timing_level': 'segment'}]
            candidates.append(Candidate(f'candidate_{index:03d}', 'filler_speech', ranges, '嗯', ['嗯'], {},
                                        group_id=f'group_{index // 2}'))
        fake = FakeLLM()
        build_decisions(candidates, AnalysisSettings(llm_enabled=True, model='test', batch_size=3), fake)
        self.assertEqual([len(r.payload['candidates']) for r in fake.requests], [2, 2, 2])
        self.assertTrue(all(len({c['group_id'] for c in r.payload['candidates']}) == 1 for r in fake.requests))

    def test_malformed_llm_aborts_without_output_or_transcript_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'project_transcript.json'
            path.write_text(json.dumps(project([Segment('嗯', 0, 1)])))
            before = path.read_bytes()
            class BadLLM:
                def generate_structured(self, request):
                    return {'decisions': [{'candidate_id': 'invented', 'action': 'DELETE', 'confidence': 1, 'reason': 'bad'}]}
            with self.assertLogs('core.analysis.pipeline', level='ERROR'), self.assertRaises(PipelineError):
                analyze_to_directory(path, AnalysisSettings(llm_enabled=True, model='test'), provider=BadLLM())
            self.assertFalse((path.parent / 'edit-analysis').exists())
            self.assertEqual(before, path.read_bytes())

    def test_normalized_but_not_verbatim_repetition_is_kept(self):
        data = project([Segment('Use OpenAI API today.', 0, 2), Segment('Use openai api today.', 2, 4)])
        plan = self.run_plan(data)
        self.assertEqual(plan['actions'][0]['action'], 'KEEP')

    def test_empty_transcript_is_keep_only(self):
        data = project([], durations=[5])
        candidate = detect_candidates(data, AnalysisSettings())[0]
        self.assertEqual(candidate.allowed_actions, ['KEEP'])
        self.assertEqual(self.run_plan(data)['actions'][0]['action'], 'KEEP')

    def test_configuration_validation(self):
        for settings in [AnalysisSettings(mode='unknown'), AnalysisSettings(retries=-1), AnalysisSettings(timeout=0),
                         AnalysisSettings(pause_thresholds=[.5,.4,1,2]), AnalysisSettings(target_pause_duration=2),
                         AnalysisSettings(llm_enabled=True), AnalysisSettings(base_url='https://key:secret@example.test')]:
            with self.assertRaises(PipelineError):
                settings.validate()
