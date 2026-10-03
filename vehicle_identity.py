"""External multimodal vehicle maker/model identification.

The service exposes one provider-neutral result shape. ChatGPT is tried first
when both credentials are configured; Gemini is used for provider, network,
quota, and malformed-response failures. A failure never stops plate
recognition because this service is called from a background worker.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROMPT = """あなたは車両識別の補助員です。添付された車両画像から、外観で判断できる範囲のメーカー名とモデル名を識別してください。
ナンバープレートの文字は読み取らず、画像に写る車両の外観だけを使ってください。
確信がない場合は manufacturer と model を unknown にしてください。推測を断定しないでください。
次のJSONオブジェクトだけを返してください（Markdownのコードブロックや説明文は不要です）。
{"manufacturer":"メーカー名またはunknown","model":"モデル名またはunknown","confidence":0.0,"reason":"短い根拠またはunknown"}
confidence はメーカーとモデルの組合せに対する0〜1の主観的な確度です。"""


class VehicleIdentityError(Exception):
    """The provider was reachable but did not return usable JSON."""


def _bounded_text(value, limit=120):
    if not isinstance(value, str):
        return 'unknown'
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', value).strip()
    return value[:limit] or 'unknown'


def parse_identity(text):
    """Parse provider text into the common, bounded result structure."""
    if not isinstance(text, str):
        raise VehicleIdentityError('外部AIの応答が文字列ではありません。')
    text = text.strip()
    decoder = json.JSONDecoder()
    payload = None
    for match in re.finditer(r'\{', text):
        try:
            payload, _ = decoder.raw_decode(text[match.start():])
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(payload, dict):
        raise VehicleIdentityError('外部AIの応答にJSONがありません。')
    if not all(isinstance(payload.get(key), str) and payload[key].strip()
               for key in ('manufacturer', 'model')) or 'confidence' not in payload:
        raise VehicleIdentityError('外部AIの応答に識別項目がありません。')
    try:
        confidence = float(payload.get('confidence', 0))
    except (TypeError, ValueError) as error:
        raise VehicleIdentityError('外部AIの確度が数値ではありません。') from error
    if not math.isfinite(confidence):
        raise VehicleIdentityError('外部AIの確度が有限値ではありません。')
    return {
        'manufacturer': _bounded_text(payload.get('manufacturer')),
        'model': _bounded_text(payload.get('model')),
        'confidence': max(0.0, min(1.0, confidence)),
        'reason': _bounded_text(payload.get('reason'), 240),
    }


def _response_text(payload):
    if not isinstance(payload, dict):
        raise VehicleIdentityError('ChatGPTの応答形式が不正です。')
    text = payload.get('output_text')
    if isinstance(text, str) and text.strip():
        return text
    for item in payload.get('output', []):
        for content in item.get('content', []) if isinstance(item, dict) else []:
            if isinstance(content, dict) and isinstance(content.get('text'), str):
                return content['text']
    raise VehicleIdentityError('ChatGPTの応答テキストがありません。')


def _http_json(url, body, headers, timeout):
    request = Request(url, data=json.dumps(body).encode('utf-8'), headers={
        **headers, 'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
    except HTTPError as error:
        raise VehicleIdentityError(f'外部AI HTTPエラー ({error.code})') from error
    except (URLError, TimeoutError, OSError) as error:
        raise VehicleIdentityError(f'外部AI通信エラー ({type(error).__name__})') from error
    if len(raw) > 2_000_000:
        raise VehicleIdentityError('外部AIの応答が大きすぎます。')
    try:
        return json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VehicleIdentityError('外部AIの応答JSONを解釈できません。') from error


class ChatGPTProvider:
    name = 'chatgpt'

    def __init__(self, api_key, model='gpt-4.1-mini', endpoint='https://api.openai.com/v1/responses', timeout=20):
        self.api_key, self.model, self.endpoint = api_key, model, endpoint
        self.timeout = max(2, min(60, int(timeout)))

    def identify(self, image_bytes):
        encoded = base64.b64encode(image_bytes).decode('ascii')
        body = {'model': self.model, 'store': False, 'max_output_tokens': 512,
                'input': [{'role': 'user', 'content': [
            {'type': 'input_text', 'text': PROMPT},
            {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,' + encoded},
        ]}]}
        payload = _http_json(self.endpoint, body, {'Authorization': 'Bearer ' + self.api_key}, self.timeout)
        return parse_identity(_response_text(payload))


class GeminiProvider:
    name = 'gemini'

    def __init__(self, api_key, model='gemini-3.8-flash', endpoint=None, timeout=20):
        self.api_key, self.model = api_key, model
        self.endpoint = endpoint or ('https://generativelanguage.googleapis.com/v1beta/models/'
                                     + model + ':generateContent')
        self.timeout = max(2, min(60, int(timeout)))

    def identify(self, image_bytes):
        body = {'contents': [{'parts': [
            {'inline_data': {'mime_type': 'image/jpeg',
                             'data': base64.b64encode(image_bytes).decode('ascii')}},
            {'text': PROMPT},
        ]}], 'generationConfig': {'temperature': 0, 'responseMimeType': 'application/json'}}
        payload = _http_json(self.endpoint, body, {'x-goog-api-key': self.api_key}, self.timeout)
        try:
            parts = payload['candidates'][0]['content']['parts']
            text = ''.join(part['text'] for part in parts if isinstance(part, dict)
                           and isinstance(part.get('text'), str) and not part.get('thought'))
        except (KeyError, IndexError, TypeError) as error:
            raise VehicleIdentityError('Geminiの応答テキストがありません。') from error
        return parse_identity(text)


class VehicleIdentityService:
    """Provider failover and common result formatting."""

    def __init__(self, providers=(), enabled=True):
        self.providers = tuple(providers)
        self.enabled = bool(enabled and self.providers)

    @classmethod
    def from_environment(cls, offline=False):
        enabled = os.getenv('GATE_VEHICLE_AI_ENABLED', '1').lower() not in {'0', 'false', 'no', 'off'}
        try:
            timeout = int(os.getenv('GATE_VEHICLE_AI_TIMEOUT', '20'))
        except ValueError:
            timeout = 20
        providers = []
        openai_key = os.getenv('GATE_OPENAI_API_KEY') or os.getenv('OPENAI_API_KEY')
        gemini_key = (os.getenv('GATE_GEMINI_API_KEY') or os.getenv('GEMINI_API_KEY') or
                      os.getenv('GOOGLE_API_KEY'))
        if not offline and enabled and openai_key:
            providers.append(ChatGPTProvider(openai_key,
                os.getenv('GATE_OPENAI_MODEL', 'gpt-4.1-mini'), timeout=timeout))
        if not offline and enabled and gemini_key:
            providers.append(GeminiProvider(gemini_key,
                os.getenv('GATE_GEMINI_MODEL', 'gemini-3.8-flash'), timeout=timeout))
        return cls(providers, enabled=enabled and not offline)

    def initial_result(self):
        if self.enabled:
            return {'status': 'pending', 'provider': None, 'manufacturer': None,
                    'model': None, 'confidence': None}
        return {'status': 'disabled', 'provider': None, 'manufacturer': None,
                'model': None, 'confidence': None}

    def identify(self, image_bytes):
        if not self.enabled:
            return self.initial_result()
        errors = []
        for provider in self.providers:
            try:
                result = provider.identify(image_bytes)
                result.update(provider=provider.name,
                              status='identified' if result['manufacturer'] != 'unknown' or
                              result['model'] != 'unknown' else 'uncertain')
                return result
            except (VehicleIdentityError, ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
                errors.append(provider.name + ':' + type(error).__name__)
        return {'status': 'unavailable', 'provider': None, 'manufacturer': None,
                'model': None, 'confidence': None, 'error': ','.join(errors)}


class BoundedIdentityExecutor:
    """Limit retained images without waiting in the recognition thread."""

    def __init__(self, max_pending=16):
        self.slots = threading.BoundedSemaphore(max_pending)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='vehicle-ai')

    def submit(self, callback, *args):
        if not self.slots.acquire(blocking=False):
            return None
        try:
            future = self.executor.submit(callback, *args)
        except Exception:
            self.slots.release()
            raise
        future.add_done_callback(lambda _future: self.slots.release())
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        self.executor.shutdown(wait=wait, cancel_futures=cancel_futures)
