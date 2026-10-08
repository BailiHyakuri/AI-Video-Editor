import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from core.config.analysis_settings import AnalysisSettings
from core.errors import PipelineError
from core.llm.base import DECISION_SCHEMA, StructuredRequest
from core.llm.compatible_provider import OpenAICompatibleProvider
from core.llm.openai_provider import OpenAIProvider, create_client, parse_response


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.settings = AnalysisSettings(llm_enabled=True, model='configured-model', timeout=17, retries=3)
        self.request = StructuredRequest('External prompt instructions.', {'candidates': []}, DECISION_SCHEMA)

    def test_openai_uses_official_responses_structured_output(self):
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(status='completed', output_text='{"decisions": []}')
        provider = OpenAIProvider(self.settings, client)
        self.assertEqual(provider.generate_structured(self.request), {'decisions': []})
        args = client.responses.create.call_args.kwargs
        self.assertEqual(args['model'], 'configured-model')
        self.assertFalse(args['store'])
        self.assertEqual(args['instructions'], self.request.instructions)
        self.assertEqual(args['text']['format']['schema'], DECISION_SCHEMA)
        self.assertTrue(args['text']['format']['strict'])
        self.assertNotIn('tools', args)

    def test_compatible_gateway_contract(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='{"decisions": []}'))])
        settings = AnalysisSettings(llm_enabled=True, provider='openai-compatible', model='gateway-model', base_url='http://localhost:8000/v1')
        self.assertEqual(OpenAICompatibleProvider(settings, client).generate_structured(self.request), {'decisions': []})
        args = client.chat.completions.create.call_args.kwargs
        self.assertEqual(args['response_format']['json_schema']['schema'], DECISION_SCHEMA)
        self.assertEqual(args['messages'][0]['content'], self.request.instructions)

    def test_timeout_retries_environment_and_sdk_lazy_loading(self):
        constructor = Mock(return_value='client')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake-runtime-test-key'}), \
             patch.dict('sys.modules', {'openai': SimpleNamespace(OpenAI=constructor)}):
            self.assertEqual(create_client(self.settings, 'OPENAI_API_KEY', 'https://api.openai.com/v1'), 'client')
        args = constructor.call_args.kwargs
        self.assertEqual(args['timeout'], 17)
        self.assertEqual(args['max_retries'], 3)
        self.assertEqual(args['api_key'], 'fake-runtime-test-key')

    def test_missing_key_and_missing_optional_sdk(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(PipelineError, 'OPENAI_API_KEY.*no-llm'):
                OpenAIProvider(self.settings)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake-runtime-test-key'}), \
             patch.dict('sys.modules', {'openai': None}):
            with self.assertRaisesRegex(PipelineError, 'SDK.*missing'):
                OpenAIProvider(self.settings)

    def test_incomplete_refused_malformed_and_duplicate_json(self):
        client = Mock()
        provider = OpenAIProvider(self.settings, client)
        client.responses.create.return_value = SimpleNamespace(status='incomplete', output_text='{"decisions": []}')
        with self.assertRaisesRegex(PipelineError, 'incomplete'):
            provider.generate_structured(self.request)
        for text in (None, '', 'not-json', '[]', '{"decisions":[],"decisions":[]}'):
            with self.assertRaises(PipelineError):
                parse_response(text)
        client.responses.create.return_value = SimpleNamespace(status='completed', output_text='')
        with self.assertRaisesRegex(PipelineError, 'refusal'):
            provider.generate_structured(self.request)

    def test_safe_api_errors_do_not_echo_secrets_or_transcript(self):
        client = Mock()
        error = RuntimeError('API_KEY=secret transcript=private')
        error.status_code = 401
        client.responses.create.side_effect = error
        with self.assertRaises(PipelineError) as caught:
            OpenAIProvider(self.settings, client).generate_structured(self.request)
        message = str(caught.exception)
        self.assertIn('HTTP 401', message)
        self.assertIn('permissions', message)
        self.assertNotIn('secret', message)
        self.assertNotIn('private', message)
