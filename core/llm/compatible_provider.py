"""Generic Chat Completions gateway; requires strict JSON-schema support."""
import json
from core.errors import PipelineError
from core.llm.openai_provider import api_error, create_client, parse_response


class OpenAICompatibleProvider:
    def __init__(self, settings, client=None):
        self.model = settings.model
        self.client = client if client is not None else create_client(settings, 'LLM_API_KEY', settings.base_url)

    def generate_structured(self, request):
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{'role': 'system', 'content': request.instructions},
                          {'role': 'user', 'content': json.dumps(request.payload, ensure_ascii=False)}],
                response_format={'type': 'json_schema', 'json_schema': {
                    'name': 'candidate_decisions', 'strict': True, 'schema': request.schema}})
        except Exception as exc:
            raise api_error(exc) from exc
        if not response.choices or response.choices[0].finish_reason != 'stop':
            raise PipelineError('Compatible provider returned incomplete decisions; no plan was saved.')
        return parse_response(response.choices[0].message.content)
