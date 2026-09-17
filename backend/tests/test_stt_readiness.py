import httpx
from pydantic import SecretStr

from app import stt


def _configured(monkeypatch):
    monkeypatch.setattr(stt, "_runtime_configuration", lambda: (True, "http://synthetic-stt", 30))
    monkeypatch.setattr(stt.settings, "stt_shared_token", SecretStr("synthetic-token"))


def test_stt_readiness_reports_loaded_service_without_sending_audio(monkeypatch):
    _configured(monkeypatch)
    calls = []

    def get(url, *, timeout):
        calls.append((url, timeout))
        return httpx.Response(
            200,
            json={"ok": True, "loaded": True},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(stt.httpx, "get", get)
    result = stt.stt_readiness()
    assert result["ready"] is True and result["status"] == "ready"
    assert calls == [("http://synthetic-stt/health", 2.0)]


def test_stt_readiness_distinguishes_unavailable_timeout_and_starting(monkeypatch):
    _configured(monkeypatch)

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("synthetic", request=httpx.Request("GET", "http://synthetic-stt/health"))

    monkeypatch.setattr(stt.httpx, "get", unavailable)
    assert stt.stt_readiness()["status"] == "unavailable"

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("synthetic", request=httpx.Request("GET", "http://synthetic-stt/health"))

    monkeypatch.setattr(stt.httpx, "get", timeout)
    assert stt.stt_readiness()["status"] == "timeout"

    def starting(url, *, timeout):
        return httpx.Response(
            200,
            json={"ok": True, "loaded": False},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(stt.httpx, "get", starting)
    assert stt.stt_readiness()["status"] == "starting"
