"""Small server-side client retaining the existing Ollama environment contract."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url=None, model=None):
        self.base_url = (base_url or os.getenv('OLLAMA_BASE_URL') or 'http://127.0.0.1:11434').rstrip('/')
        self.model = model or os.getenv('OLLAMA_MODEL') or 'qwen3.5:9b'
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('OLLAMA_BASE_URL must be an HTTP(S) server URL without credentials or query parameters.')

    def _request(self, path, payload=None, timeout=120):
        raw = None if payload is None else json.dumps(payload).encode('utf-8')
        request = Request(self.base_url + path, data=raw, headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read(2_000_001))
            if not isinstance(data, dict) or data.get('error'):
                raise ValueError('Invalid response')
            return data
        except HTTPError as exc:
            raise OllamaError('Ollama could not serve this request. Check that the configured model is installed.') from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise OllamaError('Kippy cannot reach Ollama, or the response timed out. Check the desktop connection and try again. Your conversation and materials are saved.') from exc
        except (ValueError, UnicodeError) as exc:
            raise OllamaError('Ollama returned an unreadable response. Please try again.') from exc

    def status(self):
        try:
            models = self._request('/api/tags', timeout=5).get('models', [])
            if not isinstance(models, list):
                raise OllamaError('Ollama returned an invalid model list.')
            available = any(isinstance(m, dict) and m.get('name') == self.model for m in models)
            return {'connected': True, 'model_available': available, 'model': self.model,
                    'message': 'Qwen connected' if available else 'Ollama is online; the configured model is missing.'}
        except OllamaError as exc:
            return {'connected': False, 'model_available': False, 'model': self.model, 'message': str(exc)}

    def chat(self, messages):
        result = self._request('/api/chat', {'model': self.model, 'stream': False, 'think': False,
                              'messages': messages, 'options': {'num_ctx': 16384, 'num_predict': 1800, 'temperature': 0.25}})
        content = result.get('message', {}).get('content', '')
        if not isinstance(content, str) or not content.strip():
            raise OllamaError('Qwen returned an empty answer. Please try again.')
        if result.get('done_reason') == 'length':
            raise OllamaError('Qwen reached its response limit. Ask for a smaller section or a shorter answer; this response was not presented as complete.')
        return content.strip()
