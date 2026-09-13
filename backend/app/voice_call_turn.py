from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import settings


logger = logging.getLogger(__name__)

_CLOUDFLARE_TURN_API_BASE = "https://rtc.live.cloudflare.com/v1/turn/keys"
_cache_lock = threading.Lock()
_cached_key_id = ""
_cached_until = 0.0
_cached_ice_servers: list[dict[str, object]] = []
_BROWSER_BLOCKED_PORT_53 = re.compile(r":53(?:\?|$)")


class VoiceCallTurnError(RuntimeError):
    pass


def clear_voice_call_turn_cache() -> None:
    global _cached_key_id, _cached_until, _cached_ice_servers
    with _cache_lock:
        _cached_key_id = ""
        _cached_until = 0.0
        _cached_ice_servers = []


def _cloudflare_turn_token() -> str:
    configured = settings.voice_call_cloudflare_turn_api_token
    if configured is not None:
        value = configured.get_secret_value().strip()
        if value:
            return value
    token_path = Path(settings.voice_call_cloudflare_turn_api_token_file)
    if token_path.is_file():
        return token_path.read_text(encoding="utf-8").strip()
    return ""


def _coturn_auth_secret() -> str:
    configured = settings.voice_call_coturn_auth_secret
    if configured is not None:
        value = configured.get_secret_value().strip()
        if value:
            return value
    secret_path = Path(settings.voice_call_coturn_auth_secret_file)
    if secret_path.is_file():
        return secret_path.read_text(encoding="utf-8").strip()
    return ""


def _turn_urls() -> list[str]:
    return [
        item.strip()
        for item in settings.voice_call_turn_urls.split(",")
        if item.strip().startswith(("turn:", "turns:"))
        and not _BROWSER_BLOCKED_PORT_53.search(item.strip())
    ][:12]


def _generate_coturn_ice_servers(
    secret: str,
    *,
    subject: str | None,
) -> list[dict[str, object]]:
    turn_urls = _turn_urls()
    if not turn_urls:
        raise VoiceCallTurnError("자체 TURN 중계 주소가 설정되지 않았습니다.")

    safe_subject = re.sub(r"[^A-Za-z0-9._-]", "-", subject or "mesil")[:64]
    expires_at = int(time.time()) + settings.voice_call_coturn_ttl_seconds
    username = f"{expires_at}:{safe_subject}"
    digest = hmac.new(
        secret.encode("utf-8"),
        username.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    credential = base64.b64encode(digest).decode("ascii")
    stun_servers = [
        dict(server)
        for server in settings.voice_call_ice_servers
        if not any(
            url.startswith(("turn:", "turns:"))
            for url in server.get("urls", [])
            if isinstance(url, str)
        )
    ]
    return [
        *stun_servers,
        {
            "urls": turn_urls,
            "username": username,
            "credential": credential,
        },
    ]


def _normalize_ice_servers(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("iceServers"), list):
        raise VoiceCallTurnError("TURN 응답 형식이 올바르지 않습니다.")

    normalized: list[dict[str, object]] = []
    has_turn = False
    for raw_server in payload["iceServers"][:12]:
        if not isinstance(raw_server, dict):
            continue
        raw_urls = raw_server.get("urls")
        if isinstance(raw_urls, str):
            raw_urls = [raw_urls]
        if not isinstance(raw_urls, list):
            continue
        urls = [
            value.strip()
            for value in raw_urls
            if isinstance(value, str)
            and value.strip().startswith(("stun:", "turn:", "turns:"))
            and not _BROWSER_BLOCKED_PORT_53.search(value.strip())
        ][:12]
        if not urls:
            continue
        server: dict[str, object] = {"urls": urls}
        username = raw_server.get("username")
        credential = raw_server.get("credential")
        if any(url.startswith(("turn:", "turns:")) for url in urls):
            if not isinstance(username, str) or not isinstance(credential, str):
                continue
            if not username or not credential:
                continue
            server["username"] = username
            server["credential"] = credential
            has_turn = True
        normalized.append(server)

    if not has_turn:
        raise VoiceCallTurnError("TURN 중계 주소가 발급되지 않았습니다.")
    return normalized


def _generate_cloudflare_ice_servers(key_id: str, token: str) -> list[dict[str, object]]:
    url = (
        f"{_CLOUDFLARE_TURN_API_BASE}/{quote(key_id, safe='')}"
        "/credentials/generate-ice-servers"
    )
    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"ttl": settings.voice_call_cloudflare_turn_ttl_seconds},
            timeout=settings.voice_call_cloudflare_turn_timeout_seconds,
        )
        response.raise_for_status()
        return _normalize_ice_servers(response.json())
    except VoiceCallTurnError:
        raise
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Cloudflare TURN 자격증명 발급 실패: status=%s",
            exc.response.status_code,
        )
        raise VoiceCallTurnError("TURN 자격증명을 발급하지 못했습니다.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Cloudflare TURN 자격증명 연결 실패: error_type=%s",
            type(exc).__name__,
        )
        raise VoiceCallTurnError("TURN 서버에 연결하지 못했습니다.") from exc


def voice_call_ice_servers(*, subject: str | None = None) -> list[dict[str, object]]:
    """Return static or short-lived coturn/Cloudflare ICE credentials."""
    global _cached_key_id, _cached_until, _cached_ice_servers
    key_id = (settings.voice_call_cloudflare_turn_key_id or "").strip()
    if not key_id:
        turn_urls = _turn_urls()
        coturn_secret = _coturn_auth_secret()
        if turn_urls and coturn_secret:
            return _generate_coturn_ice_servers(
                coturn_secret,
                subject=subject,
            )
        if turn_urls and not (
            settings.voice_call_turn_username
            and settings.voice_call_turn_credential is not None
        ):
            raise VoiceCallTurnError("자체 TURN 인증키가 설정되지 않았습니다.")
        return settings.voice_call_ice_servers

    token = _cloudflare_turn_token()
    if not token:
        raise VoiceCallTurnError("TURN API 토큰이 설정되지 않았습니다.")

    now = time.monotonic()
    with _cache_lock:
        if (
            _cached_key_id == key_id
            and _cached_ice_servers
            and now < _cached_until
        ):
            return [dict(server) for server in _cached_ice_servers]

        servers = _generate_cloudflare_ice_servers(key_id, token)
        cache_seconds = max(
            60,
            settings.voice_call_cloudflare_turn_ttl_seconds - 300,
        )
        _cached_key_id = key_id
        _cached_until = now + cache_seconds
        _cached_ice_servers = [dict(server) for server in servers]
        return [dict(server) for server in servers]
