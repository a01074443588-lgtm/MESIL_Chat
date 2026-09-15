from __future__ import annotations

import os
import tempfile
from pathlib import Path
from threading import RLock

from pydantic import SecretStr

from .ai_system_schemas import AiSystemProviderId
from .config import settings


_LOCK = RLock()
_SECRET_ENV_NAMES: dict[AiSystemProviderId, str] = {
    "nvidia": "NVIDIA_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama_cloud": "OLLAMA_API_KEY",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
}
_SETTINGS_SECRET_ATTRIBUTES: dict[AiSystemProviderId, str] = {
    "nvidia": "nvidia_api_key",
    "openai": "openai_api_key",
    "gemini": "gemini_api_key",
    "anthropic": "anthropic_api_key",
    "ollama_cloud": "ollama_cloud_api_key",
    "openai_compatible": "openai_compatible_api_key",
}


def provider_secret_env_name(provider: AiSystemProviderId) -> str | None:
    return _SECRET_ENV_NAMES.get(provider)


def _secret_path(provider: AiSystemProviderId) -> Path:
    if provider not in _SECRET_ENV_NAMES:
        raise ValueError("이 공급자는 API 키를 사용하지 않습니다.")
    root = Path(settings.ai_secret_dir).expanduser().resolve()
    return root / f"{provider}.key"


def _read_secret_file(path: Path) -> SecretStr | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return SecretStr(value) if value else None


def get_provider_secret(provider: AiSystemProviderId) -> SecretStr | None:
    env_name = provider_secret_env_name(provider)
    if env_name:
        environment_value = os.environ.get(env_name, "").strip()
        if environment_value:
            return SecretStr(environment_value)

    attribute = _SETTINGS_SECRET_ATTRIBUTES.get(provider)
    configured_value = getattr(settings, attribute, None) if attribute else None
    if isinstance(configured_value, SecretStr) and configured_value.get_secret_value().strip():
        return configured_value

    stored = _read_secret_file(_secret_path(provider)) if env_name else None
    if stored is not None:
        return stored

    if provider == "nvidia":
        return _read_secret_file(Path(settings.nvidia_api_key_file).expanduser().resolve())
    return None


def provider_secret_configured(provider: AiSystemProviderId) -> bool:
    return get_provider_secret(provider) is not None


def provider_secret_persistence_revision(provider: AiSystemProviderId) -> str | None:
    """Return a non-secret revision for safe probe reuse.

    Environment and pydantic settings secrets have no durable change marker, so
    callers must re-run a provider probe after a process restart. Protected
    files can be bound to their metadata without storing key material or a key
    derivative.
    """

    env_name = provider_secret_env_name(provider)
    if env_name:
        if os.environ.get(env_name, "").strip():
            return None
        attribute = _SETTINGS_SECRET_ATTRIBUTES.get(provider)
        configured_value = getattr(settings, attribute, None) if attribute else None
        if (
            isinstance(configured_value, SecretStr)
            and configured_value.get_secret_value().strip()
        ):
            return None

        candidates = [_secret_path(provider)]
        if provider == "nvidia":
            candidates.append(
                Path(settings.nvidia_api_key_file).expanduser().resolve()
            )
        for path in candidates:
            try:
                if not path.read_text(encoding="utf-8").strip():
                    continue
                metadata = path.stat()
            except OSError:
                continue
            return f"protected-file:{metadata.st_mtime_ns}:{metadata.st_size}"
    return "no-persisted-credential"


def save_provider_secret(provider: AiSystemProviderId, secret: SecretStr) -> None:
    value = secret.get_secret_value().strip()
    if len(value) < 8 or len(value) > 4096 or any(ord(character) < 32 for character in value):
        raise ValueError("API 키 형식이 올바르지 않습니다.")
    path = _secret_path(provider)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"{provider}-",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        finally:
            if temporary.exists():
                temporary.unlink()
