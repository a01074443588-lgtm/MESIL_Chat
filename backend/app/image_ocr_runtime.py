"""Central, local-only image routing; no image, prompt or error-body logging."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import re

from .ai_settings_store import load_ai_settings, effective_central_models, central_feature_selection, effective_provider_settings
from .config import settings
from .record_text_ai import local_json_request


class OcrError(RuntimeError):
    """Safe image-processing failure, compatible with existing OCR callers."""


class ImageOcrFailure(OcrError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ImageOcrRuntime:
    provider: str
    model: str
    base_url: str


_ACTIVE = ContextVar('image_ocr_runtime', default=None)


def requested_image_runtime() -> ImageOcrRuntime:
    if settings.environment == 'test' and settings.ocr_provider == 'stub':
        return ImageOcrRuntime('stub', settings.ocr_model, '')
    document, stored = load_ai_settings()
    selected = central_feature_selection(effective_central_models(document), 'image_reading') if stored else None
    if selected:
        provider = effective_provider_settings(document).get(selected.provider)
        return ImageOcrRuntime(selected.provider, selected.model, (provider.base_url if provider else '') or '')
    return ImageOcrRuntime(settings.ocr_provider, settings.ocr_model, settings.ocr_base_url)


def resolve_image_runtime(*, prefer_fallback=False) -> ImageOcrRuntime:
    requested = requested_image_runtime()
    if requested.provider == 'stub' and settings.environment == 'test':
        return requested
    document, _ = load_ai_settings()
    policy = effective_central_models(document)
    candidates = [requested]
    if policy.vision_fallback_model:
        provider = effective_provider_settings(document).get(policy.default_provider)
        candidates.append(ImageOcrRuntime(policy.default_provider, policy.vision_fallback_model, (provider.base_url if provider else '') or ''))
    # Retain only the explicitly configured local compatibility image role.
    legacy = document.model_roles.vision
    if legacy and legacy.provider == 'ollama':
        provider = effective_provider_settings(document).get(legacy.provider)
        candidates.append(ImageOcrRuntime(legacy.provider, legacy.model, (provider.base_url if provider else '') or ''))
    candidates = list(dict.fromkeys(candidates))
    if prefer_fallback and len(candidates) > 1:
        candidates = candidates[1:] + candidates[:1]
    unsupported = False
    for candidate in candidates:
        if candidate.provider != 'ollama' or not re.fullmatch(r'[A-Za-z0-9_./:-]{1,200}', candidate.model) or 'cloud' in candidate.model.lower():
            continue
        try:
            info = local_json_request(candidate.base_url, '/api/show', {'model': candidate.model}, 3)
        except Exception:
            continue
        if info.get('remote_host') or info.get('remote_model'):
            continue
        if 'vision' not in info.get('capabilities', []):
            unsupported = True
            continue
        return candidate
    if unsupported:
        raise ImageOcrFailure('image_model_unsupported', '선택한 모델이 사진 입력을 지원하지 않습니다. 관리자가 이미지 대체 모델의 연결을 확인해야 합니다.')
    raise ImageOcrFailure('image_model_unavailable', '내부 이미지 모델에 연결하지 못했습니다. 연결과 이미지 대체 모델 설정을 확인한 뒤 다시 판독해 주세요.')


def current_image_runtime() -> ImageOcrRuntime:
    active = _ACTIVE.get()
    return active[0] if active else resolve_image_runtime()


def image_provider() -> str:
    active = _ACTIVE.get()
    return active[0].provider if active else settings.ocr_provider.strip().lower()


def image_chat(payload: dict) -> dict:
    runtime = current_image_runtime()
    active = _ACTIVE.get()
    if active:
        if active[1]['model_call_count'] >= active[1]['model_call_limit']:
            raise ImageOcrFailure('image_retry_limit', '사진을 나누어 판독했지만 결과를 만들지 못했습니다. 더 가까이 촬영하거나 관리자에게 알려 주세요.')
        active[1]['model_call_count'] += 1
    payload = {**payload, 'model': runtime.model}
    try:
        response = local_json_request(runtime.base_url, '/api/chat', payload, settings.ocr_timeout_seconds)
    except Exception as exc:
        raise ImageOcrFailure('image_model_call_failed', '이미지 판독 모델의 응답을 받지 못했습니다. 연결 상태를 확인한 뒤 다시 판독해 주세요.') from exc
    if response.get('model') != runtime.model or response.get('done') is not True:
        raise ImageOcrFailure('image_model_response_invalid', '이미지 모델이 판독을 끝내지 못했습니다. 다시 판독하거나 관리자에게 알려 주세요.')
    return response


@contextmanager
def attachment_image_runtime(metadata: dict, *, retry=False):
    requested = requested_image_runtime()
    metadata.update(kind='image_ocr_runtime', requested_provider=requested.provider, requested_model=requested.model, model_call_count=0, model_call_limit=3 if retry else 1)
    runtime = resolve_image_runtime(prefer_fallback=retry)
    metadata.update(provider=runtime.provider, model=runtime.model, fallback_used=runtime != requested, image_capability_verified=runtime.provider=='ollama')
    token = _ACTIVE.set((runtime, metadata))
    try:
        yield runtime
    finally:
        _ACTIVE.reset(token)


def safe_image_failure(error: Exception) -> tuple[str, str]:
    if isinstance(error, ImageOcrFailure):
        return error.code, str(error)
    message = str(error)
    if '반복' in message or '이면지 글자 간섭' in message:
        return 'output_blocked', '판독문이 비정상적으로 반복되어 표시하지 않았습니다. 보정된 사진이나 이미지 대체 모델로 다시 판독해 주세요.'
    if '판독된 글이 없' in message or '글자를 찾지 못' in message:
        return 'no_text_detected', '사진에서 판독할 글자를 확인하지 못했습니다. 글씨가 있다면 다시 판독해 주세요.'
    return 'image_processing_failed', '판독 결과를 만들지 못했습니다. 다시 판독하거나 관리자에게 알려 주세요.'
