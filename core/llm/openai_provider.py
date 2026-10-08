"""Optional official SDK boundary; imported SDK only when LLM is enabled."""
import json
import os
from core.errors import PipelineError


def parse_response(text):
    if not isinstance(text, str) or not text.strip():
        raise PipelineError('LLM returned no structured decisions (possibly a refusal).')
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON object key')
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError('Expected an object')
        return value
    except ValueError as exc:
        raise PipelineError('LLM returned malformed structured JSON; no plan was saved.') from exc


def api_error(exc):
    # Do not echo SDK errors that may contain request text or secrets.
    status = getattr(exc, 'status_code', None)
    if status in (401, 403):
        hint = 'Check the API key and model permissions.'
    elif status == 429:
        hint = 'Rate limit or quota exceeded; retry later or check account quota.'
    elif status == 400:
        hint = 'Check model support for strict structured outputs and configured endpoint.'
    else:
        hint = 'Check network access, timeout, endpoint and model configuration.'
    return PipelineError(f'LLM request failed ({type(exc).__name__}' + (f', HTTP {status}' if status else '') + f'). {hint}')


def create_client(settings, key_env, base_url):
    secret = os.environ.get(key_env)
    if not secret:
        raise PipelineError(f'{key_env} is missing. Set it in the environment, or use --no-llm.')
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise PipelineError('Optional OpenAI SDK is missing. Install requirements-llm.txt, or use --no-llm.') from exc
    try:
        return OpenAI(api_key=secret, base_url=base_url, timeout=settings.timeout, max_retries=settings.retries)
    except Exception as exc:
        raise api_error(exc) from exc


class OpenAIProvider:
    def __init__(self, settings, client=None):
        self.model = settings.model
        self.client = client if client is not None else create_client(settings, 'OPENAI_API_KEY', 'https://api.openai.com/v1')

    def generate_structured(self, request):
        try:
            response = self.client.responses.create(
                model=self.model, instructions=request.instructions,
                input=json.dumps(request.payload, ensure_ascii=False), store=False,
                text={'format': {'type': 'json_schema', 'name': 'candidate_decisions',
                                 'strict': True, 'schema': request.schema}})
        except Exception as exc:
            raise api_error(exc) from exc
        if response.status != 'completed':
            raise PipelineError('LLM response was incomplete or failed; no edit plan was saved.')
        return parse_response(response.output_text)
