from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app import main as app_main
from app.main import _build_search_summary_structure, _compact_search_overview


def _entry(
    *, created_at: datetime, resident_id=None, resident_name=None, body="일반 보고"
):
    return {
        "number": 1,
        "message_id": uuid4(),
        "created_at": created_at.isoformat(),
        "sender": "시험 직원",
        "resident": resident_name,
        "resident_names": [resident_name] if resident_name else [],
        "resident_refs": (
            [{"id": resident_id, "name": resident_name}]
            if resident_id and resident_name
            else []
        ),
        "body": body,
        "comments": [],
        "attachment_text": "",
        "source_label": "대화",
        "action_status": None,
    }


def test_long_all_resident_search_uses_overview_cards():
    first_resident_id = uuid4()
    second_resident_id = uuid4()
    entries = [
        _entry(
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            resident_id=first_resident_id,
            resident_name="첫째 어르신",
            body="혈압 변화 확인",
        ),
        _entry(
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            resident_id=second_resident_id,
            resident_name="둘째 어르신",
            body="연락 필요",
        ),
    ]

    result = _build_search_summary_structure(
        entries,
        resident_id=None,
        requested_from=date(2026, 6, 1),
        requested_to=date(2026, 8, 1),
    )

    assert result["display_mode"] == "overview"
    assert result["period_granularity"] == "week"
    assert result["counts"] == {
        "total": 2,
        "general": 0,
        "attention": 1,
        "unresolved": 1,
    }
    assert {item["resident_name"] for item in result["resident_summaries"]} == {
        "첫째 어르신",
        "둘째 어르신",
    }
    assert "일반 보고 0건" in _compact_search_overview(result)


def test_specific_or_short_search_skips_card_intermediate_step():
    resident_id = uuid4()
    entries = [
        _entry(
            created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
            resident_id=resident_id,
            resident_name="선택 어르신",
        )
    ]

    specific = _build_search_summary_structure(
        entries,
        resident_id=resident_id,
        requested_from=date(2026, 1, 1),
        requested_to=date(2026, 8, 10),
    )
    short = _build_search_summary_structure(
        entries,
        resident_id=None,
        requested_from=date(2026, 8, 4),
        requested_to=date(2026, 8, 10),
    )

    assert specific["display_mode"] == "direct"
    assert short["display_mode"] == "direct"
    assert short["period_granularity"] == "day"


def test_specific_resident_summary_excludes_other_residents_on_shared_message():
    selected_id = uuid4()
    other_id = uuid4()
    entry = _entry(
        created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        resident_id=selected_id,
        resident_name="selected resident",
        body="selected resident report and other resident report",
    )
    entry["resident_refs"].append({"id": other_id, "name": "other resident"})

    result = _build_search_summary_structure(
        [entry],
        resident_id=selected_id,
        requested_from=date(2026, 1, 1),
        requested_to=date(2026, 8, 10),
    )

    assert result["display_mode"] == "direct"
    assert [item["resident_name"] for item in result["resident_summaries"]] == [
        "selected resident"
    ]


def test_unexpected_ai_provider_error_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(
        app_main,
        "_codex_search_summary",
        lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("provider bug")),
    )
    monkeypatch.setattr(app_main, "nemotron_is_configured", lambda: False)
    monkeypatch.setattr(
        app_main,
        "local_summary_provider_status",
        lambda: (False, "not ready"),
    )

    result, reason, cache_hit = app_main._search_summary_with_fallback(
        [],
        preferred_provider="codex",
        resident_names=[],
        fallback_summary="safe fallback",
    )

    assert result.provider == "rules"
    assert result.summary == "safe fallback"
    assert "Codex" in (reason or "")
    assert cache_hit is False


def test_selected_local_search_uses_configured_runtime_timeout(monkeypatch):
    seen: dict[str, object] = {}
    app_main._search_summary_cache.clear()
    monkeypatch.setattr(
        app_main, "local_summary_provider_status", lambda: (True, "ready")
    )
    monkeypatch.setattr(app_main.settings, "ai_review_timeout_seconds", 45)

    def summarize(**kwargs):
        seen.update(kwargs)
        return app_main.RoomSummaryResult(
            summary="local summary",
            provider="ollama",
            model="test-local",
            elapsed_ms=1,
        )

    monkeypatch.setattr(app_main, "summarize_room_messages", summarize)
    result, reason, cache_hit = app_main._search_summary_with_fallback(
        [{"number": 1, "body": "unique local summary source"}],
        preferred_provider="local",
        resident_names=[],
        fallback_summary="safe fallback",
    )

    assert result.provider == "ollama"
    assert reason is None
    assert cache_hit is False
    assert seen["preferred_provider"] == "local"
    assert seen["timeout_seconds"] == 45

    cached_result, cached_reason, cached_hit = app_main._search_summary_with_fallback(
        [{"number": 1, "body": "unique local summary source"}],
        preferred_provider="local",
        resident_names=[],
        fallback_summary="safe fallback",
    )
    assert cached_result.summary == "local summary"
    assert cached_reason is None
    assert cached_hit is True


def test_codex_search_summary_serializes_uuid_metadata(monkeypatch):
    class FakeWorker:
        def __init__(self, **kwargs):
            pass

        def health(self):
            return SimpleNamespace(credential_ready=True)

        def analyze(self, **kwargs):
            assert kwargs["sources"]
            assert str(resident_id) in kwargs["sources"][0].text
            return SimpleNamespace(
                result=SimpleNamespace(answer="summary"),
                model="test-model",
                elapsed_ms=1,
            )

    resident_id = uuid4()
    monkeypatch.setattr(app_main, "CodexWorkerClient", FakeWorker)
    monkeypatch.setattr(
        app_main.AiAssistRuntimeSettings,
        "from_env",
        classmethod(
            lambda cls: SimpleNamespace(
                worker_url="http://worker",
                worker_token="token",
                worker_timeout_seconds=20,
            )
        ),
    )

    result = app_main._codex_search_summary(
        [{"message_id": resident_id, "body": "report"}],
        resident_names=[],
    )

    assert result.provider == "codex_worker"
    assert result.summary == "summary"
