from copy import deepcopy
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app import main as main_module
from app.database import SessionLocal
from app.main import app
from app.models import (
    AttachmentTextExtraction,
    Message,
    MessageAttachment,
    MessageResidentLink,
    User,
)


ORIGIN = {"origin": "http://testserver"}


def _post_json(client: TestClient, path: str, payload: dict):
    return client.post(path, json=payload, headers=ORIGIN)


def _login_admin(client: TestClient) -> None:
    response = _post_json(
        client,
        "/api/auth/login",
        {"username": "admin", "password": "AdminPass!234"},
    )
    assert response.status_code == 200, response.text


def test_specific_resident_api_ignores_candidate_link_and_sends_selected_paragraphs(
    monkeypatch,
):
    suffix = uuid4().hex[:8]
    selected_name = f"선택시험{suffix}"
    other_name = f"다른시험{suffix}"
    captured: dict[str, object] = {}

    async def capture_provider(*, facts, **_kwargs):
        captured["facts"] = deepcopy(facts)
        return {
            "processing_method": "rules",
            "generation_verified": False,
            "model_used": None,
            "ai_elapsed_ms": 0,
            "error_type": "synthetic_fallback",
        }

    monkeypatch.setattr(
        main_module,
        "generate_search_summary",
        capture_provider,
    )

    with TestClient(app) as client:
        _login_admin(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        selected = _post_json(
            client,
            "/api/admin/residents",
            {"display_name": selected_name, "service_type": "daycare"},
        )
        other = _post_json(
            client,
            "/api/admin/residents",
            {"display_name": other_name, "service_type": "daycare"},
        )
        assert selected.status_code == 201, selected.text
        assert other.status_code == 201, other.text
        selected_id = selected.json()["id"]
        other_id = other.json()["id"]

        sent = _post_json(
            client,
            f"/api/rooms/{room_id}/messages",
            {
                "body": (
                    f"{selected_name}: 선택 대상 본문 기록.\n"
                    f"{other_name}: 제외해야 할 다른 대상 본문."
                ),
                "message_type": "chat",
                "resident_ids": [selected_id],
            },
        )
        assert sent.status_code == 201, sent.text
        message_id = sent.json()["id"]
        comment = _post_json(
            client,
            f"/api/messages/{message_id}/comments",
            {
                "body": (
                    f"{selected_name}: 선택 대상 답글.\n"
                    f"{other_name}: 제외해야 할 다른 대상 답글."
                )
            },
        )
        assert comment.status_code == 201, comment.text

        with SessionLocal() as db:
            message = db.get(Message, UUID(message_id))
            # An explicit sender selection suppresses new automatic links. Add
            # a historical candidate directly so this test remains focused on
            # the summary API's confirmed-link isolation contract.
            db.add(
                MessageResidentLink(
                    organization_id=message.organization_id,
                    message_id=message.id,
                    resident_id=UUID(other_id),
                    source="text_exact",
                    status="candidate",
                )
            )
            for ordinal, (mime_type, label) in enumerate(
                [
                    ("image/png", "이미지 판독문"),
                    ("audio/wav", "음성 받아쓰기"),
                ]
            ):
                attachment = MessageAttachment(
                    organization_id=message.organization_id,
                    message_id=message.id,
                    uploader_id=message.sender_id,
                    storage_key=f"synthetic-{uuid4().hex}",
                    original_name=f"synthetic-{ordinal}",
                    mime_type=mime_type,
                    size_bytes=1,
                    upload_ordinal=ordinal,
                    sha256="0" * 64,
                )
                db.add(attachment)
                db.flush()
                db.add(
                    AttachmentTextExtraction(
                        organization_id=message.organization_id,
                        attachment_id=attachment.id,
                        status="reviewed",
                        provider="test",
                        model_name="test",
                        extracted_text="합성 원시 판독문",
                        original_extracted_text="합성 원시 판독문",
                        reviewed_text=(
                            f"{selected_name}: 선택 대상 {label} 확정문.\n"
                            f"{other_name}: 제외해야 할 다른 대상 {label}."
                        ),
                        requested_by_id=message.sender_id,
                        reviewed_by_id=message.sender_id,
                        reviewed_at=message.created_at,
                    )
                )
            db.commit()

        summarized = _post_json(
            client,
            f"/api/rooms/{room_id}/message-search/summary",
            {
                "message_ids": [message_id],
                "provider": "rules",
                "resident_id": selected_id,
            },
        )

    assert summarized.status_code == 200, summarized.text
    assert summarized.json()["source_message_ids"] == [message_id]
    provider_facts = captured["facts"]
    assert isinstance(provider_facts, list) and len(provider_facts) >= 3
    assert {str(fact["message_id"]) for fact in provider_facts} == {message_id}
    assert {fact["resident_name"] for fact in provider_facts} == {selected_name}
    provider_text = "\n".join(fact["summary"] for fact in provider_facts)
    assert "선택 대상 본문 기록" in provider_text
    assert "선택 대상 답글" in provider_text
    assert "선택 대상 이미지 판독문 확정문" in provider_text
    assert "선택 대상 음성 받아쓰기 확정문" in provider_text
    assert "제외해야 할 다른 대상" not in provider_text


def test_room_staff_search_summary_excludes_admin_only_comment(monkeypatch):
    captured: dict[str, object] = {}

    async def capture_provider(*, facts, **_kwargs):
        captured["facts"] = deepcopy(facts)
        return {
            "processing_method": "rules",
            "generation_verified": False,
            "model_used": None,
            "ai_elapsed_ms": 0,
            "error_type": "synthetic_fallback",
        }

    monkeypatch.setattr(main_module, "generate_search_summary", capture_provider)
    suffix = uuid4().hex[:8]
    username = f"summary_staff_{suffix}"
    password = "SyntheticPass!234"
    with TestClient(app) as admin:
        _login_admin(admin)
        employee = _post_json(
            admin,
            "/api/employees",
            {
                "username": username,
                "full_name": f"합성 요약직원 {suffix}",
                "password": password,
                "role": "staff",
            },
        )
        assert employee.status_code == 201, employee.text
        created_room = _post_json(
            admin,
            "/api/admin/rooms",
            {
                "name": f"합성 코멘트 권한방 {suffix}",
                "kind": "custom",
                "member_ids": [employee.json()["id"], admin.get("/api/auth/me").json()["id"]],
                "resident_scope": "all",
            },
        )
        assert created_room.status_code == 201, created_room.text
        room_id = UUID(created_room.json()["id"])
        sent = _post_json(
            admin,
            f"/api/rooms/{room_id}/messages",
            {"body": "합성 최초 사건 기록", "message_type": "chat"},
        )
        assert sent.status_code == 201, sent.text
        message_id = sent.json()["id"]
        reply = _post_json(
            admin,
            f"/api/messages/{message_id}/comments",
            {"body": "합성 답글에서 이후 회복을 확인함"},
        )
        assert reply.status_code == 201, reply.text
        with SessionLocal() as db:
            staff_user = db.query(User).filter(User.username == username).one()
            staff_user.must_change_password = False
            db.commit()
        with TestClient(app) as staff:
            login = _post_json(
                staff,
                "/api/auth/login",
                {"username": username, "password": password},
            )
            assert login.status_code == 200, login.text
            summarized = _post_json(
                staff,
                f"/api/rooms/{room_id}/message-search/summary",
                {"message_ids": [message_id], "provider": "rules"},
            )
    assert summarized.status_code == 200, summarized.text
    provider_text = "\n".join(
        str(fact["summary"]) for fact in captured["facts"]  # type: ignore[index]
    )
    assert "합성 최초 사건 기록" in provider_text
    assert "합성 답글에서 이후 회복을 확인함" not in provider_text
    assert "합성 답글에서 이후 회복을 확인함" not in summarized.text


def test_search_summary_returns_only_verified_sentence_evidence_and_reuses_user_cache(monkeypatch):
    calls = 0

    async def verified_provider(*, facts, **_kwargs):
        nonlocal calls
        calls += 1
        latest = facts[-1]
        return {
            "processing_method": "local_ai",
            "generation_verified": True,
            "model_used": "synthetic-local-model",
            "ai_elapsed_ms": 17,
            "model_status_ms": 2,
            "draft_ms": 7,
            "review_ms": 4,
            "validation_ms": 1,
            "sentences": [{
                "text": "합성 기록의 최근 상태를 확인했습니다.",
                "summary_type": "latest",
                "resident_id": latest.get("resident_id"),
                "evidence_ids": [latest["message_id"]],
                "first_event_id": latest["message_id"],
                "follow_up_ids": [],
                "latest_record_id": latest["message_id"],
                "needs_follow_up": False,
                "unconfirmed_part": "",
            }],
            "answer": "합성 기록의 최근 상태를 확인했습니다.",
            "evidence_ids": [latest["message_id"]],
            "candidate_count": len(facts),
            "deduplicated_count": 0,
        }

    monkeypatch.setattr(main_module, "generate_search_summary", verified_provider)
    main_module._adaptive_search_summary_cache.clear()
    with TestClient(app) as client:
        _login_admin(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        first = _post_json(
            client,
            f"/api/rooms/{room_id}/messages",
            {"body": f"합성 변화 {uuid4().hex}", "message_type": "chat"},
        )
        second = _post_json(
            client,
            f"/api/rooms/{room_id}/messages",
            {"body": f"합성 후속 {uuid4().hex}", "message_type": "chat"},
        )
        assert first.status_code == second.status_code == 201
        payload = {
            "message_ids": [first.json()["id"], second.json()["id"]],
            "provider": "auto",
        }
        initial = _post_json(client, f"/api/rooms/{room_id}/message-search/summary", payload)
        cached = _post_json(client, f"/api/rooms/{room_id}/message-search/summary", payload)

    assert initial.status_code == cached.status_code == 200
    initial_payload = initial.json()
    assert initial_payload["generation_verified"] is True
    assert initial_payload["processing_method"] == "local_ai"
    assert len(initial_payload["summary_sentences"]) == 1
    assert len(initial_payload["summary_evidence"]) == 1
    assert initial_payload["summary_evidence"][0]["message_id"] == second.json()["id"]
    assert cached.json()["cache_hit"] is True
    assert calls == 1


def test_search_summary_rejects_message_ids_outside_submitted_filters(monkeypatch):
    called = False

    async def provider(**_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(main_module, "generate_search_summary", provider)
    with TestClient(app) as client:
        _login_admin(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        sent = _post_json(
            client,
            f"/api/rooms/{room_id}/messages",
            {"body": f"합성 필터 {uuid4().hex}", "message_type": "chat"},
        )
        assert sent.status_code == 201
        summarized = _post_json(
            client,
            f"/api/rooms/{room_id}/message-search/summary",
            {
                "message_ids": [sent.json()["id"]],
                "message_type": "notice",
            },
        )

    assert summarized.status_code == 422
    assert called is False
