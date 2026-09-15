from fastapi.testclient import TestClient
from sqlalchemy import event as sqlalchemy_event, func, select
from types import SimpleNamespace
from uuid import UUID
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import main as main_module
from app.database import SessionLocal, engine
from app.main import app
from app.ocr import NameRegionCandidate
from app.models import (
    AuditEvent,
    AttachmentCoordinateReview,
    AttachmentTextExtractionAttempt,
    FieldCareBriefingHistory,
    Message,
    MessageAttachment,
    MessageResidentLink,
    OcrCorrectionEvent,
    OcrCorrectionMemory,
    Organization,
    RecipientRoom,
    Resident,
    WorkItemDocumentDraft,
)
from app.confirmed_records import ConfirmedWorkRecord


ORIGIN = {"origin": "http://testserver"}
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00"
    b"\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)
WAV_SAMPLE = (
    b"RIFF\x24\x00\x00\x00WAVEfmt "
    b"\x10\x00\x00\x00\x01\x00\x01\x00\x40\x1f\x00\x00"
    b"\x80\x3e\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)
MP4_SAMPLE = (
    b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
    b"\x00\x00\x00\x08free"
)


def test_workdesk_source_filter_keeps_isolated_test_resident_distinguishable():
    carefor_resident = SimpleNamespace(
        service_type="facility",
        internal_code="SMCODI:carefor:facility:001",
        is_test_data=False,
    )
    isolated_fixture = SimpleNamespace(
        service_type="facility",
        internal_code="LONGITUDINAL-SYNTHETIC-TEST",
        is_test_data=True,
    )
    superseded_manual = SimpleNamespace(
        service_type="facility",
        internal_code="MANUAL:LEGACY",
        is_test_data=False,
    )

    filtered = main_module._filter_workdesk_resident_sources(
        [carefor_resident, isolated_fixture, superseded_manual]
    )

    assert filtered == [carefor_resident, isolated_fixture]


def test_period_date_chunks_are_finite_disjoint_and_cover_the_range():
    chunks = main_module._period_date_chunks(
        date(2026, 2, 27),
        date(2026, 8, 26),
    )
    assert len(chunks) == 6
    assert all((chunk_end - chunk_start).days <= 30 for chunk_start, chunk_end in chunks)
    assert chunks[0][0] == date(2026, 2, 27)
    assert chunks[-1][1] == date(2026, 8, 26)
    for previous, current in zip(chunks, chunks[1:]):
        assert current[0] == previous[1] + timedelta(days=1)


def test_period_evidence_is_separated_by_resident():
    text = (
        "시설(가명)003: 아침 식사 절반 드심. 물 섭취 권유함.\n"
        "시설(가명)011: 오전 프로그램은 피곤하다고 거부함.\n"
        "시설 018: 화장실 2회 도움드림."
    )
    resident_names = [
        "시설(가명)003",
        "시설(가명)011",
        "시설(가명)018",
    ]

    resident_003 = main_module._resident_specific_period_text(
        text,
        target_name="시설(가명)003",
        resident_names=resident_names,
    )
    resident_011 = main_module._resident_specific_period_text(
        text,
        target_name="시설(가명)011",
        resident_names=resident_names,
    )
    resident_018 = main_module._resident_specific_period_text(
        text,
        target_name="시설(가명)018",
        resident_names=resident_names,
    )

    assert "아침 식사 절반" in resident_003
    assert "오전 프로그램" not in resident_003
    assert "오전 프로그램" in resident_011
    assert "화장실 2회" not in resident_011
    assert "시설 018" in resident_018
    assert "아침 식사 절반" not in resident_018


def test_period_evidence_does_not_assign_unscoped_reply_to_last_resident():
    resident_names = ["시설(가명)003", "시설(가명)011"]
    text = (
        "시설(가명)003: 아침 식사 절반 드심.\n"
        "시설(가명)011: 오전 프로그램은 피곤하다고 거부함.\n"
        "[답글 · 사회복지사] 다음 식사량을 다시 확인해 주세요."
    )

    resident_003 = main_module._resident_specific_period_text(
        text,
        target_name="시설(가명)003",
        resident_names=resident_names,
    )
    resident_011 = main_module._resident_specific_period_text(
        text,
        target_name="시설(가명)011",
        resident_names=resident_names,
    )

    assert "다음 식사량" not in resident_003
    assert "다음 식사량" not in resident_011

    explicit_reply = (
        f"{text}\n"
        "[답글 · 사회복지사] 시설(가명)003 어르신의 다음 식사량을 "
        "다시 확인해 주세요."
    )
    resident_003_with_reply = main_module._resident_specific_period_text(
        explicit_reply,
        target_name="시설(가명)003",
        resident_names=resident_names,
    )
    resident_011_with_reply = main_module._resident_specific_period_text(
        explicit_reply,
        target_name="시설(가명)011",
        resident_names=resident_names,
    )
    assert "다음 식사량" in resident_003_with_reply
    assert "다음 식사량" not in resident_011_with_reply


def test_briefing_uses_specific_pending_checks_without_generic_ai_questions():
    safety_text = [
        "시설(가명)012 어르신이 오전 9시 20분 복도에서 비틀거리셔서 "
        "부축했고 간호팀에 전달했습니다."
    ]
    pending = main_module._briefing_pending_checks(
        safety_text,
        [],
    )
    assert pending == [
        "간호팀 전달 이후 보행상태와 어지럼 여부가 기록되지 않았습니다. "
        "다음 이동 전 담당 요양보호사가 확인하고 결과를 남겨주세요."
    ]
    risk_reason = main_module._briefing_risk_reason(safety_text, "high")
    assert "비틀거림" in risk_reason
    assert "넘어짐" not in risk_reason
    assert "출혈" not in risk_reason

    completed = main_module._briefing_pending_checks(
        [
            *safety_text,
            "오전 10시 10분 재확인 시 어지럼 호소 없고 "
            "휠체어로 이동 지원했습니다.",
        ],
        [],
    )
    assert completed == []

    same_sentence_completed = main_module._briefing_pending_checks(
        [
            "오후 2시 비틀거림 이후 부축하고 간호팀에 전달했으며, "
            "재확인 결과 어지럼과 통증 호소 없이 보행 안정 확인을 완료했습니다."
        ],
        [],
    )
    assert same_sentence_completed == []

    superseded_pending = main_module._briefing_pending_checks(
        [
            "오후 1시 20분 비틀거림이 있어 부축하고 간호팀에 전달했습니다. "
            "14:00 재확인 예정입니다.",
            "14:00 재확인은 아직 하지 않았습니다.",
            "14:00 재확인 결과 어지럼과 통증 호소 없이 보행 안정 확인을 완료했습니다.",
        ],
        [],
    )
    assert superseded_pending == []


def test_briefing_final_status_uses_only_recorded_thread_evidence():
    resolved = main_module._briefing_final_status(
        [
            "시설(가명)012 어르신이 복도에서 비틀거려 부축했습니다.",
            "오전 10시 10분 재확인 시 어지럼 호소 없고 안정됨.",
        ],
        [],
        [],
    )
    assert resolved == (
        "completed",
        "오전 10시 10분 재확인 시 어지럼 호소 없고 안정됨.",
    )

    needs_confirmation = main_module._briefing_final_status(
        ["시설(가명)010 어르신 혈압 수치 판독 확인이 필요합니다."],
        [],
        ["혈압 수치를 원본에서 확인해 주세요."],
    )
    assert needs_confirmation == (
        "needs_confirmation",
        "혈압 수치를 원본에서 확인해 주세요.",
    )

    future_recheck = main_module._briefing_final_status(
        ["시설(가명)010 어르신 상태는 내일 다시 재확인 예정입니다."],
        [],
    )
    assert future_recheck[0] == "needs_confirmation"
    assert "재확인 예정" in future_recheck[1]

    in_progress = main_module._briefing_final_status(
        ["시설(가명)003 어르신 식사량을 다음 식사 후 확인할 예정입니다."],
        ["assigned"],
        [],
    )
    assert in_progress[0] == "in_progress"
    assert "완료 결과" in in_progress[1]

    ambiguous = main_module._briefing_final_status(
        ["시설(가명)012 어르신을 부축했습니다."],
        [],
        [],
    )
    assert ambiguous[0] == "monitoring"
    assert "최종 결과" in ambiguous[1]


def test_briefing_treats_explicit_recovery_and_completion_reply_as_completed():
    texts = [
        "점심 식사량이 평소보다 적어 절반가량 섭취했습니다. "
        "저녁 식사 상태를 이어서 관찰해 주세요.",
        "저녁은 평소 식사량으로 회복했고 불편감이 없음을 확인해 "
        "관찰을 완료했습니다.",
    ]

    pending = main_module._briefing_pending_checks(texts, ["completed"])
    assert pending == []
    assert main_module._briefing_final_status(
        texts,
        ["completed"],
        pending,
    ) == (
        "completed",
        "저녁은 평소 식사량으로 회복했고 불편감이 없음을 확인해 "
        "관찰을 완료했습니다.",
    )


def test_briefing_does_not_confuse_skin_change_or_future_plan_with_completed_work():
    skin_text = [
        "시설(가명)017 어르신 엉치 발적 범위가 넓어지거나 "
        "피부 변화가 있으면 바로 알려 주세요."
    ]
    assert main_module._briefing_risk_reason(skin_text, "high").startswith("발적")

    future_contact = [
        "변화가 반복되면 간호 확인 결과를 보호자에게 함께 안내하기로 했습니다."
    ]
    assert main_module._briefing_completed_actions(future_contact) == []

    completed_contact = [
        "보행 상태를 확인해 보호자에게 안내했습니다."
    ]
    assert main_module._briefing_pending_checks(completed_contact, []) == []

    completed_request = [
        "엉치 압박을 줄이도록 체위를 변경하고 간호팀에 확인을 요청했습니다."
    ]
    assert main_module._briefing_completed_actions(completed_request) == completed_request
    completed_request_pending = main_module._briefing_pending_checks(
        [
            "시설(가명)017 어르신 엉치 부위가 붉게 보였습니다.",
            *completed_request,
        ],
        [],
    )
    assert completed_request_pending == [
        "피부 상태 확인 이후의 변화가 기록되지 않았습니다. "
        "다음 돌봄 전에 담당 요양보호사 또는 간호팀이 같은 부위를 확인해 주세요."
    ]

    future_request = [
        "엉치 압박을 줄이도록 체위변경을 요청할 예정입니다."
    ]
    assert main_module._briefing_completed_actions(future_request) == []

    continuing_plan = [
        "시설(가명)017 어르신 체위변경은 계속 시행하겠습니다."
    ]
    assert main_module._briefing_completed_actions(continuing_plan) == []

    guardian_request = [
        "보호자는 오늘 밤 수면 상태도 이어서 확인해 달라고 요청했습니다."
    ]
    assert main_module._briefing_pending_checks(guardian_request, []) == guardian_request


def test_briefing_document_candidates_are_conservative():
    normal_meal = main_module.RecordDraft.model_validate(
        main_module.build_prototype_suggestion(
            {
                "body": "시설(가명)008 어르신 점심 식사와 수분 섭취 양호함.",
                "resident_name": "시설(가명)008",
                "resident_names": ["시설(가명)008"],
            }
        )
    )
    assert main_module._briefing_daily_document_types(
        normal_meal,
        "시설(가명)008 어르신 점심 식사와 수분 섭취 양호함.",
    ) == ["care_service_record"]

    reduced_meal = main_module.RecordDraft.model_validate(
        main_module.build_prototype_suggestion(
            {
                "body": "시설(가명)003 어르신 식사량이 평소보다 적어 절반 드심.",
                "resident_name": "시설(가명)003",
                "resident_names": ["시설(가명)003"],
            }
        )
    )
    assert main_module._briefing_daily_document_types(
        reduced_meal,
        "시설(가명)003 어르신 식사량이 평소보다 적어 절반 드심.",
    ) == ["care_service_record", "nursing_log"]


def test_briefing_extracts_meal_fraction_sequence_only_from_meal_sentences():
    assert main_module._briefing_meal_fractions(
        [
            "시설(가명)003 어르신 식사량 2/3 드심.",
            "체온 37.6 확인됨. 식사량은 1/2 드심.",
            "오늘도 식사량 절반 드심.",
        ]
    ) == ["2/3", "1/2", "1/2"]


def test_briefing_classifies_staggering_as_safety():
    suggestion = main_module.RecordDraft.model_validate(
        main_module.build_prototype_suggestion(
            {
                "body": "시설(가명)012 어르신이 복도에서 비틀거리셔서 "
                "부축했고 간호팀에 전달했습니다.",
                "resident_name": "시설(가명)012",
                "resident_names": ["시설(가명)012"],
            }
        )
    )
    assert suggestion.classification == "safety"
    assert suggestion.risk_level == "high"
    assert main_module._briefing_daily_document_types(
        suggestion,
        "시설(가명)012 어르신이 복도에서 비틀거리셔서 "
        "부축했고 간호팀에 전달했습니다.",
    ) == ["nursing_log", "care_service_record"]


def test_briefing_does_not_create_documents_from_uncertain_values():
    uncertain_text = (
        "시설(가명)010 어르신 혈압 12?에 7?로 보이며 "
        "숫자 판독 확인이 필요합니다."
    )
    suggestion = main_module.RecordDraft.model_validate(
        main_module.build_prototype_suggestion(
            {
                "body": uncertain_text,
                "resident_name": "시설(가명)010",
                "resident_names": ["시설(가명)010"],
            }
        )
    )
    assert main_module._briefing_daily_document_types(
        suggestion,
        uncertain_text,
    ) == []


def test_briefing_today_schedule_requires_due_date_or_explicit_today_plan():
    current_date = date(2026, 8, 22)
    due_at = datetime(2026, 8, 22, 3, 0, tzinfo=timezone.utc)
    created_at = datetime(2026, 8, 22, 1, 0, tzinfo=timezone.utc)

    is_today_schedule, detected_due_at = main_module._briefing_today_schedule(
        [
            {
                "action_due_at": due_at,
                "created_at": created_at,
                "text": "혈압 재확인 결과를 남겨 주세요.",
            }
        ],
        current_date=current_date,
    )
    assert is_today_schedule is True
    assert detected_due_at == due_at

    yesterday_due_at = datetime(2026, 8, 21, 3, 0, tzinfo=timezone.utc)
    mixed_schedule, detected_due_at = main_module._briefing_today_schedule(
        [
            {
                "action_due_at": yesterday_due_at,
                "created_at": created_at,
                "text": "지난 확인 일정입니다.",
            },
            {
                "action_due_at": due_at,
                "created_at": created_at,
                "text": "오늘 확인 일정입니다.",
            },
        ],
        current_date=current_date,
    )
    assert mixed_schedule is True
    assert detected_due_at == due_at

    explicit_today_schedule, detected_due_at = (
        main_module._briefing_today_schedule(
            [
                {
                    "action_due_at": None,
                    "created_at": created_at,
                    "text": "오늘 보호자 상담이 예정되어 있습니다.",
                }
            ],
            current_date=current_date,
        )
    )
    assert explicit_today_schedule is True
    assert detected_due_at is None

    ordinary_today_message, _ = main_module._briefing_today_schedule(
        [
            {
                "action_due_at": None,
                "created_at": created_at,
                "text": "오늘 점심 식사량은 절반입니다.",
            }
        ],
        current_date=current_date,
    )
    assert ordinary_today_message is False


def test_briefing_importance_combines_risk_schedule_and_unfinished_work():
    current_date = date(2026, 8, 22)
    latest_at = datetime(2026, 8, 22, 1, 0, tzinfo=timezone.utc)

    urgent_unfinished_score = main_module._briefing_importance_score(
        risk_level="high",
        pending_checks=["상태 재확인이 필요합니다."],
        has_new_topic=True,
        action_priorities=["urgent"],
        action_statuses=["assigned"],
        due_at=datetime(2026, 8, 21, 3, 0, tzinfo=timezone.utc),
        is_today_schedule=False,
        is_carryover=True,
        final_status="needs_confirmation",
        latest_at=latest_at,
        current_date=current_date,
    )
    completed_routine_score = main_module._briefing_importance_score(
        risk_level="low",
        pending_checks=[],
        has_new_topic=False,
        action_priorities=["normal"],
        action_statuses=["completed"],
        due_at=None,
        is_today_schedule=False,
        is_carryover=False,
        final_status="completed",
        latest_at=latest_at,
        current_date=current_date,
    )

    assert urgent_unfinished_score >= 55
    assert urgent_unfinished_score > completed_routine_score
    assert completed_routine_score == 0


def test_routine_repositioning_does_not_create_nursing_log():
    text = "시설(가명)020 어르신 체위변경을 도와드렸고 편안하다고 하셨습니다."
    suggestion = main_module.RecordDraft.model_validate(
        main_module.build_prototype_suggestion(
            {
                "body": text,
                "resident_name": "시설(가명)020",
                "resident_names": ["시설(가명)020"],
            }
        )
    )
    assert suggestion.classification == "daily_care"
    assert main_module._briefing_daily_document_types(
        suggestion,
        text,
    ) == ["care_service_record"]


def test_multi_attachment_runner_continues_after_one_unexpected_failure(monkeypatch):
    attachment_ids = [UUID(int=1), UUID(int=2)]
    attempted: list[UUID] = []

    def run_one(attachment_id: UUID):
        attempted.append(attachment_id)
        if attachment_id == attachment_ids[0]:
            raise RuntimeError("simulated isolated failure")

    monkeypatch.setattr(main_module, "_run_attachment_text_extraction", run_one)

    main_module._run_attachment_text_extractions(attachment_ids)

    assert attempted == attachment_ids


def login(client: TestClient, username: str, password: str):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def post(client: TestClient, path: str, payload: dict):
    return client.post(path, json=payload, headers=ORIGIN)


def create_unit(client: TestClient, unit_type: str, name: str) -> str:
    response = post(
        client,
        "/api/org-units",
        {"unit_type": unit_type, "name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_floor_resident_photo_detail_and_processor_workdesk(monkeypatch):
    push_calls = []
    stt_context = {}
    ocr_calls: list[str] = []
    batch_ocr_mode = False

    def capture_push(user_ids, **kwargs):
        push_calls.append((set(user_ids), kwargs))
        return len(set(user_ids))

    def capture_transcription(path, *, mime_type, initial_prompt=None, hotwords=None):
        del path
        stt_context.update(
            {
                "mime_type": mime_type,
                "initial_prompt": initial_prompt,
                "hotwords": hotwords,
            }
        )
        return "가나타 어르신 점심 식사량 확인 부탁드립니다."

    def capture_ocr(path, **_context):
        ocr_calls.append(path.name)
        if batch_ocr_mode:
            return "가나타 어르신 시험용 손글씨 판독 결과"
        return "시험용 손글씨 판독 결과"

    def capture_name_regions(path, *, roster_names, layout_hints=None):
        del path
        del layout_hints
        if "가나다" not in roster_names:
            return []
        return [
            NameRegionCandidate(
                recognized="가나타",
                candidates=("가나다",),
                slot_index=1,
            )
        ]

    monkeypatch.setattr(main_module, "send_web_push_to_users", capture_push)
    monkeypatch.setattr(
        main_module,
        "transcribe_audio",
        capture_transcription,
    )
    monkeypatch.setattr(main_module, "extract_handwriting_text", capture_ocr)
    monkeypatch.setattr(main_module, "extract_report_text", capture_ocr)
    monkeypatch.setattr(
        main_module,
        "image_likely_contains_text",
        lambda _path: True,
    )
    monkeypatch.setattr(
        main_module,
        "extract_report_name_candidates",
        capture_name_regions,
    )
    with TestClient(app) as admin_client:
        login(admin_client, "admin", "AdminPass!234")
        business_id = create_unit(admin_client, "business", "가상 시설")
        department_id = create_unit(admin_client, "department", "가상 돌봄부")
        floor_2_id = create_unit(admin_client, "floor", "가상 2층")
        floor_3_id = create_unit(admin_client, "floor", "가상 3층")
        team_id = create_unit(admin_client, "team", "가상 돌봄팀")
        processor_team_id = create_unit(admin_client, "team", "가상 업무지원팀")
        admin_user_id = admin_client.get("/api/auth/me").json()["id"]
        admin_scope = admin_client.patch(
            f"/api/employees/{admin_user_id}",
            json={"floor_id": floor_2_id, "team_id": processor_team_id},
            headers=ORIGIN,
        )
        assert admin_scope.status_code == 200, admin_scope.text
        for room_name, floor_id in [
            ("가상 2층 직원방", floor_2_id),
            ("가상 3층 직원방", floor_3_id),
        ]:
            room_response = post(
                admin_client,
                "/api/admin/rooms",
                {
                    "name": room_name,
                    "kind": "floor",
                    "scope_unit_id": floor_id,
                    "resident_scope": "floor",
                },
            )
            assert room_response.status_code == 201, room_response.text

        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            room_2 = RecipientRoom(
                organization_id=organization.id,
                internal_code="TEST-ROOM-2",
                name="가상 2층 생활실",
                floor="가상 2층",
                floor_unit_id=UUID(floor_2_id),
            )
            room_3 = RecipientRoom(
                organization_id=organization.id,
                internal_code="TEST-ROOM-3",
                name="가상 3층 생활실",
                floor="가상 3층",
                floor_unit_id=UUID(floor_3_id),
            )
            db.add_all([room_2, room_3])
            db.flush()
            resident_2 = Resident(
                organization_id=organization.id,
                internal_code="TEST-R-2-01",
                display_name="가상 어르신 2-01",
                service_type="facility",
                room_id=room_2.id,
                is_test_data=True,
            )
            resident_3 = Resident(
                organization_id=organization.id,
                internal_code="TEST-R-3-01",
                display_name="가상 어르신 3-01",
                service_type="facility",
                room_id=room_3.id,
                is_test_data=True,
            )
            db.add_all([resident_2, resident_3])
            db.commit()
            resident_2_id = resident_2.id
            resident_3_id = resident_3.id

        staff_payload = {
            "username": "resident_writer",
            "full_name": "가상 작성자",
            "password": "WriterPass!234",
            "role": "staff",
            "business_id": business_id,
            "department_id": department_id,
            "job_code": "caregiver",
            "floor_id": floor_2_id,
            "team_id": team_id,
        }
        processor_payload = {
            "username": "record_processor",
            "full_name": "가상 처리담당자",
            "password": "ProcessPass!234",
            "role": "staff",
            "can_process_records": True,
            "business_id": business_id,
            "department_id": department_id,
            "job_code": "social_worker",
            "floor_id": floor_2_id,
            "team_id": processor_team_id,
        }
        outside_nursing_payload = {
            "username": "outside_nursing_assistant",
            "full_name": "가상 시설 간호조무사",
            "password": "NursingPass!234",
            "role": "staff",
            "can_process_records": True,
            "business_id": business_id,
            "department_id": department_id,
            "job_code": "nursing_assistant",
            "floor_id": floor_3_id,
            "team_id": processor_team_id,
        }
        third_party_payload = {
            "username": "resident_observer",
            "full_name": "가상 제3자 직원",
            "password": "ObserverPass!234",
            "role": "staff",
            "business_id": business_id,
            "department_id": department_id,
            "job_code": "caregiver",
            "floor_id": floor_2_id,
            "team_id": team_id,
        }
        staff_response = post(admin_client, "/api/employees", staff_payload)
        processor_response = post(admin_client, "/api/employees", processor_payload)
        outside_nursing_response = post(
            admin_client,
            "/api/employees",
            outside_nursing_payload,
        )
        third_party_response = post(
            admin_client,
            "/api/employees",
            third_party_payload,
        )
        assert staff_response.status_code == 201, staff_response.text
        assert processor_response.status_code == 201, processor_response.text
        assert outside_nursing_response.status_code == 201, outside_nursing_response.text
        assert third_party_response.status_code == 201, third_party_response.text
        priority_room_response = post(
            admin_client,
            "/api/admin/rooms",
            {
                "name": "가상 3층 어르신 우선 지정방",
                "kind": "custom",
                "member_ids": [staff_response.json()["id"]],
                "resident_scope": "floor",
                "resident_scope_unit_id": floor_3_id,
            },
        )
        assert priority_room_response.status_code == 201, priority_room_response.text

        writer = TestClient(app)
        processor = TestClient(app)
        outside_processor = TestClient(app)
        third_party = TestClient(app)
        login(writer, "resident_writer", "WriterPass!234")
        login(processor, "record_processor", "ProcessPass!234")
        login(
            outside_processor,
            "outside_nursing_assistant",
            "NursingPass!234",
        )
        login(third_party, "resident_observer", "ObserverPass!234")
        assert (
            post(
                writer,
                "/api/auth/password",
                {
                    "current_password": "WriterPass!234",
                    "new_password": "WriterReady!234",
                },
            ).status_code
            == 200
        )
        assert (
            post(
                processor,
                "/api/auth/password",
                {
                    "current_password": "ProcessPass!234",
                    "new_password": "ProcessReady!234",
                },
            ).status_code
            == 200
        )
        assert (
            post(
                outside_processor,
                "/api/auth/password",
                {
                    "current_password": "NursingPass!234",
                    "new_password": "NursingReady!234",
                },
            ).status_code
            == 200
        )
        assert (
            post(
                third_party,
                "/api/auth/password",
                {
                    "current_password": "ObserverPass!234",
                    "new_password": "ObserverReady!234",
                },
            ).status_code
            == 200
        )

        writer_rooms = writer.get("/api/rooms").json()
        floor_room = next(room for room in writer_rooms if room["name"] == "가상 2층 직원방")
        room_id = floor_room["id"]
        action_assignees_response = writer.get(
            f"/api/rooms/{room_id}/action-assignees"
        )
        assert action_assignees_response.status_code == 200
        action_assignee_ids = {
            candidate["id"] for candidate in action_assignees_response.json()
        }
        assert processor_response.json()["id"] in action_assignee_ids
        assert outside_nursing_response.json()["id"] not in action_assignee_ids
        residents = writer.get(f"/api/rooms/{room_id}/residents").json()
        residents_by_id = {resident["id"]: resident for resident in residents}
        assert residents[0]["id"] == str(resident_2_id)
        assert residents_by_id[str(resident_2_id)]["is_priority"]
        assert not residents_by_id[str(resident_3_id)]["is_priority"]
        priority_room_id = priority_room_response.json()["id"]
        priority_residents = writer.get(
            f"/api/rooms/{priority_room_id}/residents"
        ).json()
        priority_residents_by_id = {
            resident["id"]: resident for resident in priority_residents
        }
        assert priority_residents[0]["id"] == str(resident_3_id)
        assert priority_residents_by_id[str(resident_3_id)]["is_priority"]
        assert not priority_residents_by_id[str(resident_2_id)]["is_priority"]

        workdesk_residents = processor.get("/api/workdesk/residents")
        assert workdesk_residents.status_code == 200, workdesk_residents.text
        assert {
            resident["id"] for resident in workdesk_residents.json()
        } >= {str(resident_2_id), str(resident_3_id)}
        assert writer.get("/api/workdesk/residents").status_code == 403

        sent = writer.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={
                "body": "점심 식사량이 평소보다 적어 확인을 부탁드립니다.",
                "message_type": "chat",
                "resident_id": str(resident_2_id),
                "report_image": "true",
            },
            files={"files": ("meal.png", PNG_1X1, "image/png")},
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        message = sent.json()
        assert message["resident"]["id"] == str(resident_2_id)
        assert len(message["attachments"]) == 1
        assert message["attachments"][0]["text_extraction"]["status"] == "pending"

        processor_floor_rooms = processor.get("/api/rooms").json()
        processor_floor_room = next(
            room for room in processor_floor_rooms if room["name"] == "가상 2층 직원방"
        )
        messages = processor.get(
            f"/api/rooms/{processor_floor_room['id']}/messages"
        ).json()
        assert messages[-1]["attachments"][0]["original_name"] == "meal.png"
        assert (
            post(
                processor,
                f"/api/rooms/{room_id}/read",
                {"message_id": message["id"]},
            ).status_code
            == 204
        )
        # 댓글 작성은 관리자 전용이다. 기록 처리 권한 직원은 원문과
        # 근거를 열람·처리할 수 있어도 댓글 작성 권한까지 갖지 않는다.
        comment = post(
            admin_client,
            f"/api/messages/{message['id']}/comments",
            {"body": "사회복지사가 식사기록을 함께 확인하겠습니다."},
        )
        assert comment.status_code == 201, comment.text
        comment_push_calls = [
            call
            for call in push_calls
            if call[1].get("notification_kind") == "comment"
        ]
        assert comment_push_calls == [
            (
                    {
                        UUID(staff_response.json()["id"]),
                        UUID(processor_response.json()["id"]),
                        UUID(third_party_response.json()["id"]),
                    },
                {
                    "room_id": UUID(room_id),
                    "message_id": UUID(message["id"]),
                    "comment_id": UUID(comment.json()["id"]),
                    "notification_kind": "comment",
                },
            )
        ]
        writer_messages = writer.get(f"/api/rooms/{room_id}/messages").json()
        commented_message = next(item for item in writer_messages if item["id"] == message["id"])
        assert commented_message["comment_count"] == 0
        assert commented_message["unread_comment_count"] == 0
        assert commented_message["read_count"] == 2
        assert commented_message["reply_user_count"] == 1
        assert commented_message["latest_comment"] is None
        detail = writer.get(f"/api/messages/{message['id']}").json()
        assert {item["user_name"] for item in detail["read_receipts"]} == {
            "가상 작성자",
            "가상 처리담당자",
        }
        assert detail["comments"] == []
        assert (
            post(
                writer,
                f"/api/messages/{message['id']}/comments/read",
                {},
            ).status_code
            == 403
        )
        admin_detail = admin_client.get(f"/api/messages/{message['id']}").json()
        assert admin_detail["comments"][0]["body"].startswith("사회복지사")
        assert (
            post(
                admin_client,
                f"/api/messages/{message['id']}/comments/read",
                {},
            ).status_code
            == 204
        )
        reread = writer.get(f"/api/rooms/{room_id}/messages").json()
        assert next(item for item in reread if item["id"] == message["id"])[
            "unread_comment_count"
        ] == 0
        photo = processor.get(message["attachments"][0]["download_url"])
        assert photo.status_code == 200
        assert photo.content == PNG_1X1
        outside_room_photo = outside_processor.get(
            message["attachments"][0]["download_url"]
        )
        # The processor is outside this room but has same-business WorkItem
        # access. The shared server-side attachment policy must therefore allow
        # the original through either authenticated download route.
        assert outside_room_photo.status_code == 200
        assert outside_room_photo.content == PNG_1X1
        workdesk_photo = outside_processor.get(
            f"/api/workdesk/attachments/{message['attachments'][0]['id']}"
        )
        assert workdesk_photo.status_code == 200
        assert workdesk_photo.content == PNG_1X1

        outside_room_action = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "방 밖 직원을 직접 지정하면 안 됩니다.",
                "message_type": "work_request",
                "resident_id": str(resident_2_id),
                "action": {
                    "action_type": "cooperation",
                    "assignee_user_id": outside_nursing_response.json()["id"],
                    "priority": "normal",
                },
            },
        )
        assert outside_room_action.status_code == 422
        assert "현재 채팅방에 참여한 직원" in outside_room_action.json()["detail"]

        action_message = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "다음 근무자가 식사량을 한 번 더 확인해 주세요.",
                "message_type": "handover",
                "resident_id": str(resident_2_id),
                "action": {
                    "action_type": "handover",
                    "assignee_user_id": processor_response.json()["id"],
                    "priority": "important",
                },
            },
        )
        assert action_message.status_code == 201, action_message.text
        assert action_message.json()["action_item"]["status"] == "assigned"
        assigned_actions = processor.get("/api/action-items")
        assert assigned_actions.status_code == 200, assigned_actions.text
        action_item = assigned_actions.json()[0]
        assert action_item["action_type"] == "handover"
        acknowledged = processor.patch(
            f"/api/action-items/{action_item['id']}",
            json={"status": "acknowledged"},
            headers=ORIGIN,
        )
        assert acknowledged.status_code == 200, acknowledged.text
        assert acknowledged.json()["status"] == "acknowledged"

        digests = processor.get("/api/workdesk/room-digests?period=day")
        assert digests.status_code == 200, digests.text
        floor_digest = next(item for item in digests.json() if item["room_id"] == room_id)
        assert floor_digest["message_count"] == 2
        assert floor_digest["comment_count"] == 2
        assert floor_digest["document_counts"]["care_service_record"] >= 1

        work_items = processor.get("/api/work-items").json()
        assert len(work_items) == 2
        work_item = next(
            item for item in work_items if item["source_snapshot"]["message_id"] == message["id"]
        )
        assert work_item["status"] == "in_review"
        assert work_item["source_snapshot"]["body"] == (
            "점심 식사량이 평소보다 적어 확인을 부탁드립니다."
        )
        assert work_item["source_snapshot"]["attachment_ids"] == [
            message["attachments"][0]["id"]
        ]
        extraction = work_item["message"]["attachments"][0]["text_extraction"]
        assert extraction["status"] == "completed"
        assert extraction["extracted_text"] == "시험용 손글씨 판독 결과"
        assert extraction["original_extracted_text"] == "시험용 손글씨 판독 결과"
        assert extraction["correction_event_count"] == 0
        attachment_review_detail = processor.get(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction"
        )
        assert attachment_review_detail.status_code == 200
        assert (
            attachment_review_detail.json()["text_extraction"]["extracted_text"]
            == "시험용 손글씨 판독 결과"
        )
        forbidden_attachment_review_detail = third_party.get(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction"
        )
        assert forbidden_attachment_review_detail.status_code == 403
        assert work_item["ai_state"] == "prototype_suggested"
        assert work_item["ai_suggestion"]["classification"] == "nutrition"
        assert work_item["comments"][0]["body"].startswith("사회복지사")
        third_party_image_review = third_party.patch(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "제3자가 바꾸면 안 되는 이미지 판독문",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert third_party_image_review.status_code == 403
        assert (
            third_party_image_review.json()["detail"]
            == "이 문서의 내용을 확인·수정할 권한이 없습니다."
        )
        reviewed_extraction = processor.patch(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": (
                    "시험용 손글씨 확인 결과\n"
                    "점심 식사량 감소, 간호팀 확인 필요"
                ),
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert reviewed_extraction.status_code == 200, reviewed_extraction.text
        reviewed_payload = reviewed_extraction.json()["text_extraction"]
        assert reviewed_payload["status"] == "reviewed"
        assert reviewed_payload["review_decision"] == "direct_edit"
        assert reviewed_payload["reviewed_by_name"] == "가상 처리담당자"
        assert reviewed_payload["reviewed_at"] is not None
        assert reviewed_payload["correction_event_count"] == 1
        assert (
            reviewed_payload["original_extracted_text"]
            == "시험용 손글씨 판독 결과"
        )
        attachment_id = UUID(message["attachments"][0]["id"])
        with SessionLocal() as db:
            attachment_before = db.get(MessageAttachment, attachment_id)
            assert attachment_before is not None
            extraction_before = attachment_before.text_extraction
            assert extraction_before is not None
            immutable_before = {
                "extracted_text": extraction_before.extracted_text,
                "original_extracted_text": extraction_before.original_extracted_text,
                "suggested_text": extraction_before.suggested_text,
                "reviewed_text": extraction_before.reviewed_text,
                "status": extraction_before.status,
                "reviewed_by_id": extraction_before.reviewed_by_id,
                "reviewed_at": extraction_before.reviewed_at,
                "attachment_sha256": attachment_before.sha256,
                "attachment_storage_key": attachment_before.storage_key,
                "attempt_count": db.scalar(
                    select(func.count(AttachmentTextExtractionAttempt.id)).where(
                        AttachmentTextExtractionAttempt.extraction_id
                        == extraction_before.id
                    )
                ),
                "confirmed_record_count": db.scalar(
                    select(func.count(ConfirmedWorkRecord.id))
                ),
            }
        denied_coordinate_review = third_party.get(
            f"/api/attachments/{message['attachments'][0]['id']}/coordinate-review",
            headers=ORIGIN,
        )
        assert denied_coordinate_review.status_code == 403
        coordinate_bootstrap = processor.get(
            f"/api/attachments/{message['attachments'][0]['id']}/coordinate-review",
            headers=ORIGIN,
        )
        assert coordinate_bootstrap.status_code == 200, coordinate_bootstrap.text
        assert coordinate_bootstrap.json()["is_bootstrap"] is True
        assert coordinate_bootstrap.json()["raw_source_text"] == (
            "시험용 손글씨 판독 결과"
        )
        assert coordinate_bootstrap.json()["confirmed_source_text"].startswith(
            "시험용 손글씨 확인 결과"
        )
        automatic_coordinate_bootstrap = processor.post(
            f"/api/attachments/{message['attachments'][0]['id']}"
            "/coordinate-review/auto-locate",
            headers=ORIGIN,
        )
        assert automatic_coordinate_bootstrap.status_code == 200
        assert automatic_coordinate_bootstrap.json()["is_bootstrap"] is True
        assert automatic_coordinate_bootstrap.json()["raw_source_text"] == (
            "시험용 손글씨 판독 결과"
        )
        coordinate_payload = {
            "image_width": 1200,
            "image_height": 1600,
            "editor_version": "coordinate-editor-mvp-1",
            "document_template": "synthetic-care-note-v1",
            "regions": [
                {
                    "client_id": "manual-1",
                    "bbox": {
                        "left": 0.1,
                        "top": 0.2,
                        "width": 0.6,
                        "height": 0.08,
                        "rotation_degrees": -4.5,
                    },
                    "raw_text": "시험용 손글씨 판독 결과",
                    "corrected_text": "시험용 손글씨 확인 결과",
                    "role": "general",
                    "document_template": "synthetic-care-note-v1",
                    "section_role": "특이사항",
                    "resident_id": None,
                    "position_confidence": 1,
                    "placement_status": "confirmed",
                    "source": "manual",
                }
            ],
        }
        first_coordinate_review = processor.post(
            f"/api/attachments/{message['attachments'][0]['id']}/coordinate-review",
            json=coordinate_payload,
            headers=ORIGIN,
        )
        assert first_coordinate_review.status_code == 201, first_coordinate_review.text
        assert first_coordinate_review.json()["version_number"] == 1
        assert first_coordinate_review.json()["is_bootstrap"] is False
        assert (
            first_coordinate_review.json()["regions"][0]["bbox"][
                "rotation_degrees"
            ]
            == -4.5
        )
        coordinate_payload["regions"][0]["corrected_text"] = (
            "시험용 손글씨 최종 확인 결과"
        )
        coordinate_payload["regions"][0]["bbox"]["rotation_degrees"] = 3.25
        coordinate_payload["confirm_text"] = True
        second_coordinate_review = processor.post(
            f"/api/attachments/{message['attachments'][0]['id']}/coordinate-review",
            json=coordinate_payload,
            headers=ORIGIN,
        )
        assert second_coordinate_review.status_code == 201
        assert second_coordinate_review.json()["version_number"] == 2
        assert second_coordinate_review.json()["text_confirmation_id"] is not None
        assert second_coordinate_review.json()["text_confirmed_at"] is not None
        reopened_coordinate_review = processor.get(
            f"/api/attachments/{message['attachments'][0]['id']}/coordinate-review",
            headers=ORIGIN,
        )
        assert reopened_coordinate_review.status_code == 200
        assert reopened_coordinate_review.json()["version_number"] == 2
        assert (
            reopened_coordinate_review.json()["regions"][0]["bbox"][
                "rotation_degrees"
            ]
            == 3.25
        )
        assert (
            reopened_coordinate_review.json()["text_confirmation_id"]
            == second_coordinate_review.json()["text_confirmation_id"]
        )
        detail_after_coordinate_save = processor.get(
            f"/api/messages/{message['id']}", headers=ORIGIN
        )
        assert detail_after_coordinate_save.status_code == 200
        projected_extraction = detail_after_coordinate_save.json()["message"][
            "attachments"
        ][0]["text_extraction"]
        assert projected_extraction["latest_confirmed_text"] == (
            "시험용 손글씨 최종 확인 결과"
        )
        assert projected_extraction["latest_confirmation_source"] is None
        assert projected_extraction["latest_confirmed_at"] is not None
        assert projected_extraction["reviewed_text"] is None
        full_attachment_after_coordinate_save = processor.get(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction"
        )
        assert full_attachment_after_coordinate_save.status_code == 200
        full_projected_extraction = full_attachment_after_coordinate_save.json()[
            "text_extraction"
        ]
        assert full_projected_extraction["latest_confirmed_text"] == (
            "시험용 손글씨 최종 확인 결과"
        )
        assert full_projected_extraction["latest_confirmation_source"] == (
            "coordinate_editor"
        )
        assert full_projected_extraction["latest_confirmed_at"] is not None
        with SessionLocal() as db:
            events = db.scalars(
                select(OcrCorrectionEvent)
                .where(OcrCorrectionEvent.attachment_id == attachment_id)
                .order_by(OcrCorrectionEvent.created_at, OcrCorrectionEvent.id)
            ).all()
            assert len(events) == 2
            original_review_event, coordinate_confirmation_event = events
            assert original_review_event.raw_text == "시험용 손글씨 판독 결과"
            assert original_review_event.corrected_text.startswith(
                "시험용 손글씨 확인 결과"
            )
            assert original_review_event.confirmed is True
            assert original_review_event.coordinate_review_id is None
            assert coordinate_confirmation_event.corrected_text == (
                "시험용 손글씨 최종 확인 결과"
            )
            assert coordinate_confirmation_event.confirmed is True
            assert coordinate_confirmation_event.provider == "coordinate_editor"
            coordinate_versions = db.scalars(
                select(AttachmentCoordinateReview)
                .where(
                    AttachmentCoordinateReview.attachment_id
                    == UUID(message["attachments"][0]["id"])
                )
                .order_by(AttachmentCoordinateReview.version_number)
            ).all()
            assert [item.version_number for item in coordinate_versions] == [1, 2]
            assert coordinate_versions[0].regions[0]["corrected_text"] == (
                "시험용 손글씨 확인 결과"
            )
            assert coordinate_versions[0].regions[0]["bbox"]["rotation_degrees"] == -4.5
            assert coordinate_versions[1].regions[0]["corrected_text"] == (
                "시험용 손글씨 최종 확인 결과"
            )
            assert coordinate_versions[1].regions[0]["bbox"]["rotation_degrees"] == 3.25
            assert (
                coordinate_confirmation_event.coordinate_review_id
                == coordinate_versions[1].id
            )
            placement_evidence = (
                main_module._confirmed_coordinate_placement_evidence(
                    db,
                    organization_id=coordinate_versions[1].organization_id,
                    document_template="synthetic-care-note-v1",
                    exclude_attachment_id=UUID(int=0),
                )
            )
            assert len(placement_evidence) == 1
            assert placement_evidence[0].section_role == "특이사항"
            assert placement_evidence[0].corrected_text == (
                "시험용 손글씨 최종 확인 결과"
            )
            attachment_after = db.get(MessageAttachment, attachment_id)
            assert attachment_after is not None
            extraction_after = attachment_after.text_extraction
            assert extraction_after is not None
            assert {
                "extracted_text": extraction_after.extracted_text,
                "original_extracted_text": extraction_after.original_extracted_text,
                "suggested_text": extraction_after.suggested_text,
                "reviewed_text": extraction_after.reviewed_text,
                "status": extraction_after.status,
                "reviewed_by_id": extraction_after.reviewed_by_id,
                "reviewed_at": extraction_after.reviewed_at,
                "attachment_sha256": attachment_after.sha256,
                "attachment_storage_key": attachment_after.storage_key,
                "attempt_count": db.scalar(
                    select(func.count(AttachmentTextExtractionAttempt.id)).where(
                        AttachmentTextExtractionAttempt.extraction_id
                        == extraction_after.id
                    )
                ),
                "confirmed_record_count": db.scalar(
                    select(func.count(ConfirmedWorkRecord.id))
                ),
            } == immutable_before
            coordinate_audits = db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "attachment_coordinate_review.created",
                    AuditEvent.target_id == UUID(message["attachments"][0]["id"]),
                )
                .order_by(AuditEvent.created_at)
            ).all()
            assert len(coordinate_audits) == 2
            assert [item.details["version_number"] for item in coordinate_audits] == [
                1,
                2,
            ]
            assert all(item.actor_id is not None for item in coordinate_audits)
            confirmation_audits = db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action
                    == "attachment_coordinate_review.text_confirmed",
                    AuditEvent.target_id == attachment_id,
                )
            ).all()
            assert len(confirmation_audits) == 1
            assert any(
                pair["recognized_text"] == "판독"
                and pair["corrected_text"] == "확인"
                for pair in original_review_event.correction_pairs
            )
        empty_candidates = processor.get("/api/document-candidates")
        assert empty_candidates.status_code == 200
        assert empty_candidates.json()["total_count"] == 0
        premature_ready = processor.patch(
            f"/api/work-items/{work_item['id']}",
            json={"status": "ready"},
            headers=ORIGIN,
        )
        assert premature_ready.status_code == 422
        updated = processor.patch(
            f"/api/work-items/{work_item['id']}",
            json={
                "status": "in_review",
                "document_types": [
                    "care_service_record",
                    "nursing_log",
                ],
                "processing_notes": "가명 시험자료로 검토 중",
            },
            headers=ORIGIN,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["handled_by_name"] == "가상 처리담당자"
        assert updated.json()["document_types"] == [
            "care_service_record",
            "nursing_log",
        ]

        ai_reviewed = post(
            processor,
            f"/api/work-items/{work_item['id']}/ai-review",
            {},
        )
        assert ai_reviewed.status_code == 200, ai_reviewed.text
        proposed = ai_reviewed.json()
        assert proposed["status"] == "in_review"
        assert proposed["ai_state"] == "ai_reviewed"
        assert proposed["ai_generator"] == "stub:test-ai-review"
        assert proposed["ai_generated_at"] is not None
        assert proposed["ai_suggestion"]["classification"] == "nutrition"
        assert proposed["ai_suggestion"]["risk_level"] == "medium"
        assert "점심 식사량 감소, 간호팀 확인 필요" in proposed["ai_suggestion"][
            "corrected_text"
        ]
        assert proposed["source_snapshot"]["body"] == work_item["source_snapshot"]["body"]
        assert proposed["confirmed_at"] is None
        assert {
            document_draft["document_type"]
            for document_draft in proposed["document_drafts"]
        } == {"care_service_record", "nursing_log"}
        added_document = post(
            processor,
            (
                f"/api/work-items/{work_item['id']}/document-drafts/"
                "consultation_log"
            ),
            {},
        )
        assert added_document.status_code == 200, added_document.text
        proposed = added_document.json()
        assert "consultation_log" in proposed["ai_suggestion"]["document_types"]
        consultation_draft = next(
            document_draft
            for document_draft in proposed["document_drafts"]
            if document_draft["document_type"] == "consultation_log"
        )
        assert consultation_draft["status"] == "draft"
        assert consultation_draft["generator"] == "reviewer-added-rule-v1"
        assert "[주요 상담 내용]" in consultation_draft["content"]
        added_document_again = post(
            processor,
            (
                f"/api/work-items/{work_item['id']}/document-drafts/"
                "consultation_log"
            ),
            {},
        )
        assert added_document_again.status_code == 200
        assert (
            next(
                document_draft
                for document_draft in added_document_again.json()["document_drafts"]
                if document_draft["document_type"] == "consultation_log"
            )["version"]
            == consultation_draft["version"]
        )
        first_care_draft = next(
            document_draft
            for document_draft in proposed["document_drafts"]
            if document_draft["document_type"] == "care_service_record"
        )
        edited_document = processor.patch(
            (
                f"/api/work-items/{work_item['id']}/document-drafts/"
                "care_service_record"
            ),
            json={
                "action": "direct_edit",
                "content": first_care_draft["content"]
                + "\n담당자가 식사량 감소 내용을 확인함",
            },
            headers=ORIGIN,
        )
        assert edited_document.status_code == 200, edited_document.text
        edited_care_draft = next(
            document_draft
            for document_draft in edited_document.json()["document_drafts"]
            if document_draft["document_type"] == "care_service_record"
        )
        assert edited_care_draft["version"] == first_care_draft["version"] + 1
        assert "담당자가" in edited_care_draft["content"]

        confirmation_payload = {
            **proposed["ai_suggestion"],
            "summary": "가상 어르신의 점심 식사량 감소를 담당자가 확인함",
            "document_types": [
                "care_service_record",
                "nursing_log",
                "consultation_log",
            ],
            "reviewer_notes": "시험 제안을 검토했으며 실제 서류에는 아직 반영하지 않음",
            "verification_acknowledged": True,
        }
        confirmed = post(
            processor,
            f"/api/work-items/{work_item['id']}/confirm",
            confirmation_payload,
        )
        assert confirmed.status_code == 200, confirmed.text
        confirmed_item = confirmed.json()
        assert confirmed_item["status"] == "ready"
        assert confirmed_item["confirmed_by_name"] == "가상 처리담당자"
        assert confirmed_item["confirmed_at"] is not None
        assert confirmed_item["confirmed_record"]["summary"] == confirmation_payload["summary"]
        assert confirmed_item["processing_notes"] == confirmation_payload["reviewer_notes"]
        assert confirmed_item["source_snapshot"] == work_item["source_snapshot"]
        assert all(
            document_draft["status"] == "approved"
            for document_draft in confirmed_item["document_drafts"]
        )
        assert all(
            document_draft["approved_by_name"] == "가상 처리담당자"
            for document_draft in confirmed_item["document_drafts"]
        )
        approved_versions = {
            document_draft["document_type"]: document_draft["version"]
            for document_draft in confirmed_item["document_drafts"]
        }

        dashboard = processor.get("/api/document-candidates")
        assert dashboard.status_code == 200, dashboard.text
        dashboard_payload = dashboard.json()
        assert dashboard_payload["total_count"] == 1
        assert dashboard_payload["filtered_count"] == 1
        assert dashboard_payload["document_counts"] == {
            "care_service_record": 1,
            "nursing_log": 1,
            "consultation_log": 1,
        }
        assert dashboard_payload["risk_counts"] == {"medium": 1}
        assert dashboard_payload["classification_counts"] == {"nutrition": 1}
        assert dashboard_payload["items"][0]["id"] == work_item["id"]
        care_record_candidates = processor.get(
            "/api/document-candidates?document_type=care_service_record"
        )
        assert care_record_candidates.json()["filtered_count"] == 1
        nursing_candidates = processor.get(
            "/api/document-candidates?document_type=nursing_log"
        )
        assert nursing_candidates.json()["total_count"] == 1
        assert nursing_candidates.json()["filtered_count"] == 1
        medium_candidates = processor.get(
            "/api/document-candidates?risk_level=medium&classification=nutrition"
        )
        assert medium_candidates.json()["filtered_count"] == 1
        assert (
            processor.get("/api/document-candidates?risk_level=invalid").status_code
            == 422
        )

        regenerate = post(
            processor,
            f"/api/work-items/{work_item['id']}/prototype-suggestion",
            {},
        )
        assert regenerate.status_code == 409

        regular_staff_forbidden = writer.get("/api/work-items")
        assert regular_staff_forbidden.status_code == 403
        assert writer.get("/api/document-candidates").status_code == 403
        forbidden_prototype = post(
            writer,
            f"/api/work-items/{work_item['id']}/prototype-suggestion",
            {},
        )
        assert forbidden_prototype.status_code == 403
        writer_own_review = writer.patch(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "작성자가 직접 확인한 손글씨 확인 결과",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert writer_own_review.status_code == 200
        assert writer_own_review.json()["text_extraction"]["status"] == "reviewed"
        processor_own_review = processor.patch(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "처리담당자가 직접 확인한 손글씨 확인 결과",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert processor_own_review.status_code == 200, processor_own_review.text
        writer_review_payload = processor_own_review.json()["text_extraction"]
        assert writer_review_payload["status"] == "reviewed"
        assert writer_review_payload["reviewed_by_name"] == "가상 처리담당자"
        assert writer_review_payload["original_extracted_text"] == (
            "시험용 손글씨 판독 결과"
        )
        with SessionLocal() as db:
            image_attachment = db.get(
                MessageAttachment,
                UUID(message["attachments"][0]["id"]),
            )
            image_events = db.scalars(
                select(OcrCorrectionEvent)
                .where(OcrCorrectionEvent.extraction_id == image_attachment.text_extraction.id)
                .order_by(
                    OcrCorrectionEvent.created_at.desc(),
                    OcrCorrectionEvent.id.desc(),
                )
            ).all()
            assert len(image_events) == 4
            assert sum(
                event.coordinate_review_id is not None for event in image_events
            ) == 1
            assert image_events[0].raw_text == "작성자가 직접 확인한 손글씨 확인 결과"
            assert image_events[0].corrected_text == (
                "처리담당자가 직접 확인한 손글씨 확인 결과"
            )
            assert image_events[0].reviewed_by.full_name == "가상 처리담당자"
            image_memory = db.scalar(
                select(OcrCorrectionMemory).where(
                    OcrCorrectionMemory.recognized_text == "판독",
                    OcrCorrectionMemory.corrected_text == "확인",
                )
            )
            assert image_memory is not None
            assert image_memory.occurrence_count == 1

        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            candidate_resident = Resident(
                organization_id=organization.id,
                internal_code="TEST-R-CANDIDATE",
                display_name="가나다",
                service_type="facility",
                is_test_data=True,
            )
            db.add(candidate_resident)
            db.commit()
            candidate_resident_id = candidate_resident.id

        audio_sent = writer.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={
                "body": "",
                "message_type": "chat",
                "resident_ids": [str(resident_2_id), str(resident_3_id)],
            },
            files={"files": ("voice.wav", WAV_SAMPLE, "audio/wav")},
            headers=ORIGIN,
        )
        assert audio_sent.status_code == 201, audio_sent.text
        assert audio_sent.json()["body"] == "파일을 첨부했습니다."
        assert audio_sent.json()["attachments"][0]["mime_type"] == "audio/wav"
        assert {
            link["resident"]["id"]: link["status"]
            for link in audio_sent.json()["resident_links"]
        } == {
            str(resident_2_id): "confirmed",
            str(resident_3_id): "confirmed",
        }
        assert (
            audio_sent.json()["attachments"][0]["text_extraction"]["status"]
            == "pending"
        )
        audio_work_item = next(
            item
            for item in processor.get("/api/work-items").json()
            if item["source_snapshot"]["message_id"] == audio_sent.json()["id"]
        )
        audio_extraction = audio_work_item["message"]["attachments"][0][
            "text_extraction"
        ]
        assert audio_extraction["status"] == "completed"
        assert audio_extraction["model_name"] == "test-whisper"
        assert audio_extraction["extracted_text"] == (
            "가나타 어르신 점심 식사량 확인 부탁드립니다."
        )
        assert stt_context["mime_type"] == "audio/wav"
        assert stt_context["initial_prompt"] is None
        assert stt_context["hotwords"] is None
        roster_candidate = next(
            candidate
            for candidate in audio_extraction["spelling_candidates"]
            if candidate["candidate"] == "가나다"
        )
        assert roster_candidate["recognized"] == "가나타"
        assert roster_candidate["source"] == "active_resident_roster"
        assert roster_candidate["reason"] == "현재 이용 어르신 이름과 유사"
        assert not roster_candidate["auto_applicable"]
        third_party_audio_review = third_party.patch(
            f"/api/attachments/{audio_sent.json()['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "제3자가 바꾸면 안 되는 음성 받아쓰기",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert third_party_audio_review.status_code == 403
        assert (
            third_party_audio_review.json()["detail"]
            == "이 문서의 내용을 확인·수정할 권한이 없습니다."
        )
        writer_audio_review = writer.patch(
            f"/api/attachments/{audio_sent.json()['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "가나다 어르신 점심 식사량을 확인했습니다.",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        # Uploaders may review their own audio, but must explicitly confirm the resident selection.
        assert writer_audio_review.status_code == 422, writer_audio_review.text
        processor_audio_review = processor.patch(
            f"/api/attachments/{audio_sent.json()['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "가나다 어르신 점심 식사량을 확인했습니다.",
                "decision": "direct_edit",
                "resident_ids": [str(resident_2_id), str(resident_3_id)],
            },
            headers=ORIGIN,
        )
        assert processor_audio_review.status_code == 200, processor_audio_review.text
        assert processor_audio_review.json()["text_extraction"]["status"] == "reviewed"
        assert processor_audio_review.json()["text_extraction"]["reviewed_by_name"] == (
            "가상 처리담당자"
        )
        assert processor_audio_review.json()["text_extraction"][
            "original_extracted_text"
        ] == "가나타 어르신 점심 식사량 확인 부탁드립니다."
        reviewed_audio_message = writer.get(
            f"/api/messages/{audio_sent.json()['id']}",
            headers=ORIGIN,
        )
        assert reviewed_audio_message.status_code == 200
        reviewed_audio_links = reviewed_audio_message.json()["message"][
            "resident_links"
        ]
        assert not any(
            link["status"] == "candidate" for link in reviewed_audio_links
        )
        # Directly selected residents take precedence over names inferred from
        # a transcript; staff review must not silently add another resident.
        assert {link["resident"]["id"] for link in reviewed_audio_links if link["status"] == "confirmed"} == {str(resident_2_id), str(resident_3_id)}
        assert (
            processor_audio_review.json()["text_extraction"]["correction_event_count"]
            == 1
        )

        manual_multi_resident_message = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "두 어르신을 직접 선택한 보고입니다.",
                "message_type": "report",
                "resident_ids": [str(resident_2_id), str(resident_3_id)],
            },
        )
        assert manual_multi_resident_message.status_code == 201
        manual_multi_payload = manual_multi_resident_message.json()
        assert manual_multi_payload["resident"]["id"] == str(resident_2_id)
        assert {
            link["resident"]["id"]: link["status"]
            for link in manual_multi_payload["resident_links"]
        } == {
            str(resident_2_id): "confirmed",
            str(resident_3_id): "confirmed",
        }
        manual_multi_item = next(
            item
            for item in processor.get("/api/work-items").json()
            if item["source_snapshot"]["message_id"]
            == manual_multi_payload["id"]
        )
        assert len(manual_multi_item["source_snapshot"]["resident_names"]) == 2

        multi_resident_message = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": (
                    "가상 어르신 2-01 어르신은 식사량을 확인하고, "
                    "가상 어르신 3-01 어르신은 귀가 시간을 확인해 주세요."
                ),
                "message_type": "chat",
            },
        )
        assert multi_resident_message.status_code == 201, multi_resident_message.text
        multi_payload = multi_resident_message.json()
        assert multi_payload["resident"]["id"] == str(resident_2_id)
        assert {
            link["resident"]["id"] for link in multi_payload["resident_links"]
        } == {str(resident_2_id), str(resident_3_id)}
        assert {
            link["status"] for link in multi_payload["resident_links"]
        } == {"confirmed"}

        candidate_items = processor.get("/api/work-items").json()
        multi_item = next(
            item
            for item in candidate_items
            if item["source_snapshot"]["message_id"] == multi_payload["id"]
        )
        assert multi_item["resident"]["id"] == str(resident_2_id)
        assert set(multi_item["source_snapshot"]["resident_names"]) == {
            "가상 어르신 2-01",
            "가상 어르신 3-01",
        }
        assert multi_item["ai_suggestion"] is not None
        assert "가상 어르신 2-01" in multi_item["ai_suggestion"]["summary"]
        assert "가상 어르신 3-01" in multi_item["ai_suggestion"]["summary"]

        multi_resident_digests = processor.get(
            "/api/workdesk/room-digests?period=day"
        )
        assert multi_resident_digests.status_code == 200
        updated_floor_digest = next(
            item
            for item in multi_resident_digests.json()
            if item["room_id"] == room_id
        )
        assert updated_floor_digest["resident_count"] == 2
        multi_point = next(
            point
            for point in updated_floor_digest["major_points"]
            if point["message_id"] == multi_payload["id"]
        )
        assert "가상 어르신 2-01" in multi_point["resident_name"]
        assert "가상 어르신 3-01" in multi_point["resident_name"]

        unselected_report = writer.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={
                "body": "",
                "message_type": "chat",
                "report_image": "true",
            },
            files={"files": ("multi-report.png", PNG_1X1, "image/png")},
            headers=ORIGIN,
        )
        assert unselected_report.status_code == 201, unselected_report.text
        unselected_payload = unselected_report.json()
        assert unselected_payload["resident"]["id"] == str(resident_2_id)
        assert (
            unselected_payload["attachments"][0]["text_extraction"]["status"]
            == "pending"
        )
        unselected_item = next(
            item
            for item in processor.get("/api/work-items").json()
            if item["source_snapshot"]["message_id"] == unselected_payload["id"]
        )
        assert unselected_item["resident"]["id"] == str(resident_2_id)
        second_extraction = unselected_item["message"]["attachments"][0][
            "text_extraction"
        ]
        learned_candidate = next(
            candidate
            for candidate in second_extraction["spelling_candidates"]
            if candidate["recognized"] == "판독"
            and candidate["candidate"] == "확인"
        )
        assert learned_candidate["source"] == "confirmed_history"
        assert learned_candidate["auto_applicable"] is False
        needs_review = processor.patch(
            (
                f"/api/attachments/"
                f"{unselected_payload['attachments'][0]['id']}/text-extraction"
            ),
            json={"decision": "needs_review"},
            headers=ORIGIN,
        )
        assert needs_review.status_code == 200, needs_review.text
        needs_payload = needs_review.json()["text_extraction"]
        assert needs_payload["review_decision"] == "needs_review"
        assert needs_payload["correction_event_count"] == 1
        assert needs_payload["status"] == "completed"
        assert needs_payload["reviewed_text"] is None
        applied_candidate = processor.patch(
            (
                f"/api/attachments/"
                f"{unselected_payload['attachments'][0]['id']}/text-extraction"
            ),
            json={
                "reviewed_text": "시험용 손글씨 확인 결과",
                "decision": "apply_candidate",
                "selected_candidate_id": learned_candidate["id"],
            },
            headers=ORIGIN,
        )
        assert applied_candidate.status_code == 200, applied_candidate.text
        applied_payload = applied_candidate.json()["text_extraction"]
        assert applied_payload["review_decision"] == "apply_candidate"
        assert applied_payload["correction_event_count"] == 2
        assert applied_payload["reviewed_text"] == "시험용 손글씨 확인 결과"
        assert applied_payload["original_extracted_text"] == "시험용 손글씨 판독 결과"

        correction_message = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "음성을 다시 확인하니 다른 어르신에 대한 보고였습니다.",
                "message_type": "chat",
                "resident_id": str(resident_2_id),
            },
        )
        assert correction_message.status_code == 201, correction_message.text
        correction_item = next(
            item
            for item in processor.get("/api/work-items").json()
            if item["source_snapshot"]["message_id"]
            == correction_message.json()["id"]
        )
        assert correction_item["resident"]["id"] == str(resident_2_id)
        assert correction_item["ai_suggestion"] is not None

        corrected_resident = processor.patch(
            f"/api/work-items/{correction_item['id']}/resident",
            json={"resident_id": str(resident_3_id)},
            headers=ORIGIN,
        )
        assert corrected_resident.status_code == 200, corrected_resident.text
        corrected_item = corrected_resident.json()
        assert corrected_item["resident"]["id"] == str(resident_3_id)
        assert corrected_item["source_snapshot"]["resident_name"] == "가상 어르신 3-01"
        assert corrected_item["source_snapshot"]["resident_names"] == [
            "가상 어르신 3-01"
        ]
        assert corrected_item["ai_suggestion"] is None
        links_by_resident = {
            link["resident"]["id"]: link
            for link in corrected_item["message"]["resident_links"]
        }
        assert links_by_resident[str(resident_3_id)]["status"] == "confirmed"
        with SessionLocal() as db:
            rejected_previous_link = db.scalar(
                select(MessageResidentLink).where(
                    MessageResidentLink.message_id
                    == UUID(correction_message.json()["id"]),
                    MessageResidentLink.resident_id == resident_2_id,
                )
            )
            assert rejected_previous_link is not None
            assert rejected_previous_link.status == "rejected"

        confirmed_resident_change = processor.patch(
            f"/api/work-items/{work_item['id']}/resident",
            json={"resident_id": str(resident_3_id)},
            headers=ORIGIN,
        )
        assert confirmed_resident_change.status_code == 409

        reopened = post(
            processor,
            f"/api/work-items/{work_item['id']}/reopen",
            {"reason": "승인 후 급여제공기록지 문구 수정 필요"},
        )
        assert reopened.status_code == 200, reopened.text
        reopened_item = reopened.json()
        assert reopened_item["status"] == "in_review"
        assert reopened_item["confirmed_at"] is None
        assert reopened_item["confirmed_record"] is None
        assert all(
            document_draft["status"] == "draft"
            for document_draft in reopened_item["document_drafts"]
        )
        assert {
            document_draft["document_type"]: document_draft["version"]
            for document_draft in reopened_item["document_drafts"]
        } == {
            document_type: version + 1
            for document_type, version in approved_versions.items()
        }
        with SessionLocal() as db:
            stored_drafts = db.scalars(
                select(WorkItemDocumentDraft)
                .where(
                    WorkItemDocumentDraft.work_item_id
                    == UUID(work_item["id"])
                )
                .order_by(
                    WorkItemDocumentDraft.document_type,
                    WorkItemDocumentDraft.version,
                )
            ).all()
            assert any(
                draft.status == "approved" and not draft.is_current
                for draft in stored_drafts
            )
            assert any(
                draft.status == "draft" and draft.is_current
                for draft in stored_drafts
            )
        assert (
            post(
                processor,
                f"/api/work-items/{work_item['id']}/reopen",
                {"reason": "중복 승인 취소"},
            ).status_code
            == 409
        )

        action_from_detail = post(
            writer,
            f"/api/messages/{multi_payload['id']}/action-item",
            {
                "action_type": "handover",
                "assignee_user_id": processor_response.json()["id"],
                "priority": "important",
            },
        )
        assert action_from_detail.status_code == 201, action_from_detail.text
        assert action_from_detail.json()["created_by_id"] == staff_response.json()["id"]
        detail_after_action = writer.get(
            f"/api/messages/{multi_payload['id']}"
        )
        assert detail_after_action.status_code == 200
        assert detail_after_action.json()["message"]["action_item"]["status"] == "assigned"
        assert detail_after_action.json()["comments"] == []
        admin_detail_after_action = admin_client.get(
            f"/api/messages/{multi_payload['id']}"
        )
        assert admin_detail_after_action.status_code == 200
        assert any(
            comment["body"].startswith("업무로 전달했습니다.")
            for comment in admin_detail_after_action.json()["comments"]
        )
        action_completed = processor.patch(
            f"/api/action-items/{action_from_detail.json()['id']}",
            json={"status": "completed"},
            headers=ORIGIN,
        )
        assert action_completed.status_code == 200, action_completed.text
        assert action_completed.json()["status"] == "completed"
        detail_after_complete = writer.get(
            f"/api/messages/{multi_payload['id']}"
        ).json()
        assert detail_after_complete["comments"] == []
        admin_detail_after_complete = admin_client.get(
            f"/api/messages/{multi_payload['id']}"
        ).json()
        assert any(
            comment["body"] == "업무 처리를 완료했습니다."
            for comment in admin_detail_after_complete["comments"]
        )

        completed_search = writer.get(
            f"/api/rooms/{room_id}/message-search",
            params={
                "q": "업무 처리를 완료했습니다",
                "action_status": "completed",
            },
        )
        assert completed_search.status_code == 200, completed_search.text
        assert completed_search.json()["matched_count"] == 1
        assert completed_search.json()["messages"][0]["id"] == multi_payload["id"]
        search_query_count = 0

        def count_search_query(*_args):
            nonlocal search_query_count
            search_query_count += 1

        sqlalchemy_event.listen(engine, "before_cursor_execute", count_search_query)
        try:
            ocr_search = writer.get(
                f"/api/rooms/{room_id}/message-search",
                params={"q": "처리담당자가 직접 확인한 손글씨 확인 결과"},
            )
        finally:
            sqlalchemy_event.remove(engine, "before_cursor_execute", count_search_query)
        assert ocr_search.status_code == 200, ocr_search.text
        assert search_query_count <= 60
        assert message["id"] in {
            searched["id"] for searched in ocr_search.json()["messages"]
        }
        ocr_matches = [
            match
            for match in ocr_search.json()["matches"]
            if match["message_id"] == message["id"]
        ]
        assert any(match["source_type"] == "image" for match in ocr_matches)
        assert any(
            "처리담당자가 직접 확인한 손글씨 확인 결과" in match["excerpt"]
            for match in ocr_matches
        )
        summarized = post(
            writer,
            f"/api/rooms/{room_id}/message-search/summary",
            {"message_ids": [multi_payload["id"]]},
        )
        assert summarized.status_code == 200, summarized.text
        # No central text model is configured by conftest; source-bound rules
        # are the expected fallback instead of the retired legacy stub route.
        assert summarized.json()["generator"] == "rules:adaptive-search-summary-v1"
        assert "검색 결과 1건" not in summarized.json()["summary"]
        assert summarized.json()["summary_sentences"]
        assert summarized.json()["summary_evidence"]
        assert summarized.json()["source_message_ids"] == [multi_payload["id"]]
        assert summarized.json()["display_mode"] == "overview"
        assert summarized.json()["counts"]["total"] == 1
        assert summarized.json()["resident_summaries"]
        assert summarized.json()["resident_summaries"][0]["evidence"][0]["message_id"] == multi_payload["id"]

        optional_report = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "시설장에게 전달할 간단한 보고입니다.",
                "message_type": "report",
            },
        )
        assert optional_report.status_code == 201, optional_report.text
        assert optional_report.json()["message_type"] == "report"
        assert optional_report.json()["action_item"] is None

        video_sent = writer.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={
                "body": "보행 모습을 촬영한 시험 동영상입니다.",
                "message_type": "chat",
            },
            files={"files": ("walking.mp4", MP4_SAMPLE, "video/mp4")},
            headers=ORIGIN,
        )
        assert video_sent.status_code == 201, video_sent.text
        video_payload = video_sent.json()
        assert video_payload["attachments"][0]["mime_type"] == "video/mp4"
        assert video_payload["attachments"][0]["text_extraction"] is None

        carryover_message = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "선택 어르신의 혈압 재확인 결과를 남겨 주세요.",
                "message_type": "chat",
                "resident_id": str(resident_2_id),
                "action": {
                    "action_type": "confirmation",
                    "assignee_user_id": processor_response.json()["id"],
                    "priority": "important",
                },
            },
        )
        assert carryover_message.status_code == 201, carryover_message.text

        today_kst = datetime.now(ZoneInfo("Asia/Seoul"))
        with SessionLocal() as db:
            stored_carryover = db.get(Message, UUID(carryover_message.json()["id"]))
            assert stored_carryover is not None
            assert stored_carryover.action_item is not None
            stored_carryover.created_at = datetime.now(timezone.utc) - timedelta(
                days=9
            )

            stored_today_action = db.get(Message, UUID(action_message.json()["id"]))
            assert stored_today_action is not None
            assert stored_today_action.action_item is not None
            stored_today_action.action_item.due_at = today_kst.replace(
                hour=17,
                minute=0,
                second=0,
                microsecond=0,
            ).astimezone(timezone.utc)
            db.commit()

        today = today_kst.date().isoformat()
        period_review = post(
            processor,
            "/api/workdesk/period-review",
            {
                "start_date": today,
                "end_date": today,
                "room_id": room_id,
            },
        )
        assert period_review.status_code == 200, period_review.text
        period_payload = period_review.json()
        assert period_payload["message_count"] >= 2
        assert period_payload["generator"] == "quick-period-summary-v1"
        assert optional_report.json()["id"] in {
            source["message"]["id"] for source in period_payload["sources"]
        }
        assert message["id"] in {
            source["message"]["id"] for source in period_payload["sources"]
        }
        original_source = next(
            source
            for source in period_payload["sources"]
            if source["message"]["id"] == message["id"]
        )
        assert original_source["read_count"] >= 2
        assert original_source["reply_count"] >= 1
        assert original_source["reply_user_count"] >= 1
        assert original_source["comments"]
        assert original_source["comments"][0]["author_name"]
        assert original_source["comments"][0]["body"]
        assert period_payload["record_events"]
        assert period_payload["overall_summary"]
        assert period_payload["daily_care_references"]
        assert period_payload["care_reference_count"] >= 1
        assert period_payload["history_id"] is None
        assert period_payload["processed_periods"] == [today]
        assert period_payload["truncated_periods"] == []

        saved_period_review = post(
            processor,
            "/api/workdesk/period-review",
            {
                "start_date": today,
                "end_date": today,
                "room_id": room_id,
                "save_history": True,
            },
        )
        assert saved_period_review.status_code == 200, saved_period_review.text
        saved_payload = saved_period_review.json()
        assert saved_payload["history_id"]
        assert saved_payload["history_revision"] == 1
        history_list = processor.get("/api/workdesk/period-reviews")
        assert history_list.status_code == 200, history_list.text
        assert history_list.json()[0]["id"] == saved_payload["history_id"]
        history_detail = processor.get(
            f'/api/workdesk/period-reviews/{saved_payload["history_id"]}'
        )
        assert history_detail.status_code == 200, history_detail.text
        assert history_detail.json()["overall_summary"] == saved_payload[
            "overall_summary"
        ]
        with SessionLocal() as db:
            stored_history = db.get(
                FieldCareBriefingHistory,
                UUID(saved_payload["history_id"]),
            )
            assert stored_history is not None
            assert stored_history.official_record_saved is False

        empty_period_review = post(
            processor,
            "/api/workdesk/period-review",
            {
                "start_date": "2099-01-01",
                "end_date": "2099-01-07",
                "room_id": room_id,
                "save_history": True,
            },
        )
        assert empty_period_review.status_code == 200, empty_period_review.text
        empty_payload = empty_period_review.json()
        assert empty_payload["history_id"]
        assert empty_payload["history_revision"] == 1
        assert empty_payload["care_reference_count"] == 0
        assert empty_payload["consultation_reference_count"] == 0
        assert "돌봄 내용이 없습니다" in empty_payload["overall_summary"]

        requested_33_day_review = post(
            processor,
            "/api/workdesk/period-review",
            {
                "start_date": "2026-07-25",
                "end_date": "2026-08-26",
                "room_id": room_id,
            },
        )
        assert requested_33_day_review.status_code == 200, requested_33_day_review.text
        requested_33_day_payload = requested_33_day_review.json()
        assert len(requested_33_day_payload["processed_periods"]) == 2
        requested_33_day_source_ids = [
            source["message"]["id"]
            for source in requested_33_day_payload["sources"]
        ]
        assert len(requested_33_day_source_ids) == len(
            set(requested_33_day_source_ids)
        )

        three_month_review = post(
            processor,
            "/api/workdesk/period-review",
            {
                "start_date": (today_kst.date() - timedelta(days=90)).isoformat(),
                "end_date": today,
                "room_id": room_id,
            },
        )
        assert three_month_review.status_code == 200, three_month_review.text
        assert len(three_month_review.json()["processed_periods"]) == 3

        six_month_review = post(
            processor,
            "/api/workdesk/period-review",
            {
                "start_date": (today_kst.date() - timedelta(days=180)).isoformat(),
                "end_date": today,
                "room_id": room_id,
                "enhance_summary": True,
            },
        )
        assert six_month_review.status_code == 200, six_month_review.text
        six_month_payload = six_month_review.json()
        assert len(six_month_payload["processed_periods"]) == 6
        assert six_month_payload["generator"] == "quick-period-summary-v1:chunked-safe"
        six_month_source_ids = [
            source["message"]["id"] for source in six_month_payload["sources"]
        ]
        assert len(six_month_source_ids) == len(set(six_month_source_ids))

        over_six_month_review = post(
            processor,
            "/api/workdesk/period-review",
            {
                "start_date": (today_kst.date() - timedelta(days=185)).isoformat(),
                "end_date": today,
                "room_id": room_id,
            },
        )
        assert over_six_month_review.status_code == 422
        assert "최대 6개월" in str(over_six_month_review.json())
        assert set(period_payload["record_group_counts"]) == {
            "nursing",
            "care_service",
            "consultation",
            "program",
            "general",
            "needs_review",
        }
        approved_document_candidate_types = {
            "care_service_record",
            "consultation_log",
            "cognitive_function_assessment",
            "fall_risk_assessment",
            "pressure_ulcer_risk_assessment",
            "needs_assessment",
            "long_term_care_service_plan",
        }
        assert set(period_payload["document_candidate_counts"]) == (
            approved_document_candidate_types
        )
        assert all(
            set(event["document_candidate_types"])
            <= approved_document_candidate_types
            for event in period_payload["record_events"]
        )
        assert all(
            {"nursing_log", "program_log", "general", "needs_review"}.isdisjoint(
                event["document_candidate_types"]
            )
            for event in period_payload["record_events"]
        )
        assert all(
            set(event["document_candidate_reasons"]).issubset(
                event["document_candidate_types"]
            )
            for event in period_payload["record_events"]
        )
        assert all(
            set(event["document_candidate_review_reasons"]).issubset(
                event["document_candidate_review_types"]
            )
            for event in period_payload["record_events"]
        )
        first_record_event = period_payload["record_events"][0]
        assert first_record_event["event_group_id"]
        assert first_record_event["evidence_ids"]
        assert first_record_event["record_usage_tags"]
        record_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": first_record_event["record_usage_tags"][0],
                "record_usage_tags": first_record_event["record_usage_tags"],
                "selections": [
                    {
                        "resident_id": first_record_event["resident_id"],
                        "evidence_ids": first_record_event["evidence_ids"],
                    }
                ],
            },
        )
        assert record_summary.status_code == 200, record_summary.text
        assert record_summary.json()["evidence_ids"] == first_record_event["evidence_ids"]
        assert record_summary.json()["record_usage_tags"] == first_record_event[
            "record_usage_tags"
        ]
        assert record_summary.json()["generator"] == "stub:test-ai-review"
        assert record_summary.json()["summary"]

        candidate_event = next(
            event
            for event in period_payload["record_events"]
            if event["document_candidate_types"]
        )
        document_candidate_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "document_candidate_types": candidate_event[
                    "document_candidate_types"
                ],
                "selections": [
                    {
                        "resident_id": candidate_event["resident_id"],
                        "evidence_ids": candidate_event["evidence_ids"],
                    }
                ],
            },
        )
        assert document_candidate_summary.status_code == 200, (
            document_candidate_summary.text
        )
        assert document_candidate_summary.json()["document_candidate_types"] == (
            candidate_event["document_candidate_types"]
        )
        assert document_candidate_summary.json()["record_usage_tag"] is None
        assert document_candidate_summary.json()["record_usage_tags"] == []
        returned_candidate_drafts = document_candidate_summary.json()[
            "document_drafts"
        ]
        assert {
            draft["document_type"] for draft in returned_candidate_drafts
        } == set(candidate_event["document_candidate_types"])
        assert all(
            draft["status"] == "needs_confirmation"
            and draft["source_message_ids"] == candidate_event["evidence_ids"]
            for draft in returned_candidate_drafts
        )

        separate_document_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "document_candidate_types": [
                    "care_service_record",
                    "consultation_log",
                ],
                "selections": [
                    {
                        "resident_id": candidate_event["resident_id"],
                        "evidence_ids": candidate_event["evidence_ids"],
                    }
                ],
            },
        )
        assert separate_document_summary.status_code == 200, (
            separate_document_summary.text
        )
        separate_drafts = separate_document_summary.json()["document_drafts"]
        assert [draft["document_type"] for draft in separate_drafts] == [
            "care_service_record",
            "consultation_log",
        ]
        assert all(draft["status"] == "needs_confirmation" for draft in separate_drafts)
        assert separate_drafts[0]["key"] != separate_drafts[1]["key"]

        legacy_general_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": "general",
                "record_usage_tags": ["general"],
                "evidence_ids": [optional_report.json()["id"]],
            },
        )
        assert legacy_general_summary.status_code == 200, legacy_general_summary.text
        assert legacy_general_summary.json()["evidence_ids"] == [
            optional_report.json()["id"]
        ]

        legacy_single_resident_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": "care_service",
                "record_usage_tags": ["care_service"],
                "evidence_ids": [message["id"]],
            },
        )
        assert (
            legacy_single_resident_summary.status_code == 200
        ), legacy_single_resident_summary.text
        assert legacy_single_resident_summary.json()["evidence_ids"] == [message["id"]]

        mixed_contract_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": first_record_event["record_usage_tags"][0],
                "record_usage_tags": first_record_event["record_usage_tags"],
                "selections": [
                    {
                        "resident_id": first_record_event["resident_id"],
                        "evidence_ids": first_record_event["evidence_ids"],
                    }
                ],
                "evidence_ids": first_record_event["evidence_ids"],
            },
        )
        assert mixed_contract_summary.status_code == 422
        assert "동시에 사용할 수 없습니다" in mixed_contract_summary.text

        legacy_multi_resident_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": "care_service",
                "record_usage_tags": ["care_service"],
                "evidence_ids": [multi_payload["id"]],
            },
        )
        assert legacy_multi_resident_summary.status_code == 422
        assert "여러 어르신이 연결된 근거" in legacy_multi_resident_summary.json()[
            "detail"
        ]

        assert any(
            draft["resident_id"] == str(resident_2_id)
            for draft in period_payload["document_drafts"]
        )
        assert period_payload["briefing"]["comparison_days"] == 7
        assert period_payload["briefing"]["document_candidate_count"] >= 1
        assert period_payload["briefing"]["today_schedule_count"] >= 1
        assert period_payload["briefing"]["carryover_count"] >= 1
        assert set(period_payload["briefing"]).issuperset(
            {
                "needs_attention_count",
                "background_count",
                "completed_count",
            }
        )
        today_schedule_card = next(
            card
            for card in period_payload["briefing"]["cards"]
            if action_message.json()["id"] in card["source_message_ids"]
        )
        assert today_schedule_card["display_group"] == "today_schedule"
        assert today_schedule_card["due_at"]
        carryover_card = next(
            card
            for card in period_payload["briefing"]["cards"]
            if carryover_message.json()["id"] in card["source_message_ids"]
        )
        assert carryover_card["display_group"] == "carryover"
        assert carryover_card["importance_score"] >= 25
        resident_briefing = next(
            card
            for card in period_payload["briefing"]["cards"]
            if message["id"] in card["source_message_ids"]
        )
        assert resident_briefing["source_message_ids"]
        assert resident_briefing["change_summary"]
        assert "식사량" in resident_briefing["change_summary"]
        assert resident_briefing["final_status"] in {
            "needs_confirmation",
            "in_progress",
            "completed",
            "monitoring",
        }
        assert resident_briefing["final_status_summary"]
        assert resident_briefing["occurred_at"]
        assert resident_briefing["latest_at"]
        assert resident_briefing["check_reasons"]
        assert resident_briefing["priority"] in {"first", "check", "observe"}
        assert resident_briefing["display_group"] in {
            "today_schedule",
            "attention",
            "carryover",
            "background",
            "completed",
        }
        assert resident_briefing["importance_score"] >= 0
        assert set(resident_briefing["document_types"]).issubset(
            {
                "care_service_record",
                "nursing_log",
                "consultation_log",
                "physical_restraint_log",
                "program_log",
            }
        )
        resident_event_drafts = [
            draft
            for draft in period_payload["document_drafts"]
            if draft["event_group_id"] == resident_briefing["event_group_id"]
        ]
        assert {
            draft["document_type"] for draft in resident_event_drafts
        } == set(resident_briefing["document_types"])
        for draft in resident_event_drafts:
            assert draft["key"] == (
                f'{resident_briefing["event_group_id"]}:{draft["document_type"]}'
            )
            assert draft["resident_id"] == resident_briefing["resident_id"]
            assert set(draft["source_message_ids"]) == set(
                resident_briefing["source_message_ids"]
            )
        enhanced_period_review = post(
            processor,
            "/api/workdesk/period-review",
            {
                "start_date": today,
                "end_date": today,
                "room_id": room_id,
                "enhance_summary": True,
            },
        )
        assert enhanced_period_review.status_code == 200, enhanced_period_review.text
        assert enhanced_period_review.json()["generator"] == "stub:test-ai-review"
        period_video = processor.get(
            f"/api/workdesk/attachments/"
            f"{video_payload['attachments'][0]['id']}"
        )
        assert period_video.status_code == 200, period_video.text
        assert period_video.content == MP4_SAMPLE

        with SessionLocal() as db:
            inactive_linked_resident = db.get(Resident, resident_3_id)
            inactive_linked_resident.is_active = False
            db.commit()
        inactive_link_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": "care_service",
                "record_usage_tags": ["care_service"],
                "selections": [
                    {
                        "resident_id": str(resident_2_id),
                        "evidence_ids": [multi_payload["id"]],
                    }
                ],
            },
        )
        legacy_inactive_link_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": "care_service",
                "record_usage_tags": ["care_service"],
                "evidence_ids": [correction_message.json()["id"]],
            },
        )
        with SessionLocal() as db:
            inactive_linked_resident = db.get(Resident, resident_3_id)
            inactive_linked_resident.is_active = True
            db.commit()
        assert inactive_link_summary.status_code == 422
        assert "현재 확인할 수 없는 명단" in inactive_link_summary.json()["detail"]
        assert legacy_inactive_link_summary.status_code == 422
        assert "현재 확인할 수 없는 명단" in legacy_inactive_link_summary.json()[
            "detail"
        ]

        assert (
            outside_processor.get(
                f"/api/rooms/{room_id}/message-search",
                params={"q": "식사량"},
            ).status_code
            == 403
        )

        audio_second_review_denied = writer.patch(
            f"/api/attachments/{audio_sent.json()['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "가나다 어르신 점심 식사량을 다시 확인했습니다.",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert audio_second_review_denied.status_code == 422
        audio_second_review = processor.patch(
            f"/api/attachments/{audio_sent.json()['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "가나다 어르신 점심 식사량을 다시 확인했습니다.",
                "decision": "direct_edit",
                "resident_ids": [str(resident_2_id), str(resident_3_id)],
            },
            headers=ORIGIN,
        )
        assert audio_second_review.status_code == 200, audio_second_review.text
        audio_second_payload = audio_second_review.json()["text_extraction"]
        assert audio_second_payload["status"] == "reviewed"
        assert audio_second_payload["reviewed_by_name"] == "가상 처리담당자"
        assert audio_second_payload["reviewed_text"] == (
            "가나다 어르신 점심 식사량을 다시 확인했습니다."
        )
        assert audio_second_payload["original_extracted_text"] == (
            "가나타 어르신 점심 식사량 확인 부탁드립니다."
        )
        with SessionLocal() as db:
            audio_attachment = db.get(
                MessageAttachment,
                UUID(audio_sent.json()["attachments"][0]["id"]),
            )
            audio_events = db.scalars(
                select(OcrCorrectionEvent)
                .where(OcrCorrectionEvent.extraction_id == audio_attachment.text_extraction.id)
                .order_by(
                    OcrCorrectionEvent.created_at.desc(),
                    OcrCorrectionEvent.id.desc(),
                )
            ).all()
            assert len(audio_events) == 2
            assert audio_events[0].raw_text == (
                "가나다 어르신 점심 식사량을 확인했습니다."
            )
            assert audio_events[0].corrected_text == (
                "가나다 어르신 점심 식사량을 다시 확인했습니다."
            )
            assert audio_events[0].reviewed_by.full_name == "가상 처리담당자"

        repeated_audio = writer.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "", "message_type": "chat"},
            files={"files": ("voice-repeat.wav", WAV_SAMPLE, "audio/wav")},
            headers=ORIGIN,
        )
        assert repeated_audio.status_code == 201, repeated_audio.text
        repeated_audio_item = next(
            item
            for item in processor.get("/api/work-items").json()
            if item["source_snapshot"]["message_id"] == repeated_audio.json()["id"]
        )
        repeated_audio_candidates = repeated_audio_item["message"]["attachments"][0][
            "text_extraction"
        ]["spelling_candidates"]
        assert any(
            candidate["recognized"] == "가나타"
            and candidate["candidate"] == "가나다"
            and candidate["source"] == "confirmed_history"
            and candidate["auto_applicable"] is False
            for candidate in repeated_audio_candidates
        )

        ocr_call_count_before_batch = len(ocr_calls)
        batch_ocr_mode = True
        multi_image_sent = writer.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "두 장 이미지 일괄 판독 시험", "message_type": "chat", "report_image": "true"},
            files=[
                ("files", ("batch-page-1.png", PNG_1X1, "image/png")),
                ("files", ("batch-page-2.png", PNG_1X1, "image/png")),
            ],
            headers=ORIGIN,
        )
        assert multi_image_sent.status_code == 201, multi_image_sent.text
        multi_image_payload = multi_image_sent.json()
        assert len(multi_image_payload["attachments"]) == 2
        multi_image_detail = writer.get(
            f"/api/messages/{multi_image_payload['id']}"
        ).json()
        assert [
            attachment["text_extraction"]["status"]
            for attachment in multi_image_detail["message"]["attachments"]
        ] == ["completed", "completed"]
        assert len(ocr_calls) == ocr_call_count_before_batch + 2

        multi_image_message_id = UUID(multi_image_payload["id"])
        multi_image_attachment_ids = [
            UUID(attachment["id"])
            for attachment in multi_image_detail["message"]["attachments"]
        ]
        with SessionLocal() as db:
            multi_image_message = db.get(Message, multi_image_message_id)
            assert multi_image_message is not None
            scoped_link = db.scalar(
                select(MessageResidentLink).where(
                    MessageResidentLink.message_id == multi_image_message.id,
                    MessageResidentLink.resident_id == resident_2_id,
                )
            )
            assert scoped_link is not None
            scoped_link.source = "ocr_exact"
            scoped_link.status = "candidate"
            # This fixture deliberately replaces a room-confirmed link with
            # OCR-only candidates. Remove its original manual-choice intent.
            multi_image_message.extra_data = {k:v for k,v in (multi_image_message.extra_data or {}).items() if k != "resident_review"}
            multi_image_message.resident_id = None
            db.add(
                MessageResidentLink(
                    organization_id=multi_image_message.organization_id,
                    message_id=multi_image_message.id,
                    resident_id=candidate_resident_id,
                    source="ocr_exact",
                    status="candidate",
                )
            )
            db.commit()
            multi_image_immutable_before = {}
            for attachment_id in multi_image_attachment_ids:
                attachment = db.get(MessageAttachment, attachment_id)
                assert attachment is not None
                extraction = attachment.text_extraction
                assert extraction is not None
                multi_image_immutable_before[attachment_id] = {
                    "extracted_text": extraction.extracted_text,
                    "original_extracted_text": extraction.original_extracted_text,
                    "suggested_text": extraction.suggested_text,
                    "reviewed_text": extraction.reviewed_text,
                    "status": extraction.status,
                    "reviewed_by_id": extraction.reviewed_by_id,
                    "reviewed_at": extraction.reviewed_at,
                    "attachment_sha256": attachment.sha256,
                    "attachment_storage_key": attachment.storage_key,
                    "attempt_count": db.scalar(
                        select(func.count(AttachmentTextExtractionAttempt.id)).where(
                            AttachmentTextExtractionAttempt.extraction_id
                            == extraction.id
                        )
                    ),
                }
            confirmed_record_count_before_coordinate_batch = db.scalar(
                select(func.count(ConfirmedWorkRecord.id))
            )

        def coordinate_confirmation_payload(corrected_text: str) -> dict:
            return {
                "image_width": 1200,
                "image_height": 1600,
                "editor_version": "coordinate-editor-resident-link-test-1",
                "document_template": "synthetic-multi-image-note-v1",
                "confirm_text": True,
                "regions": [
                    {
                        "client_id": "confirmed-line-1",
                        "bbox": {
                            "left": 0.1,
                            "top": 0.2,
                            "width": 0.7,
                            "height": 0.08,
                            "rotation_degrees": 0,
                        },
                        "raw_text": "가나타 어르신 시험용 손글씨 판독 결과",
                        "corrected_text": corrected_text,
                        "role": "general",
                        "document_template": "synthetic-multi-image-note-v1",
                        "section_role": "특이사항",
                        "resident_id": None,
                        "position_confidence": 1,
                        "placement_status": "confirmed",
                        "source": "manual",
                    }
                ],
            }

        denied_multi_coordinate_save = third_party.post(
            f"/api/attachments/{multi_image_attachment_ids[0]}/coordinate-review",
            json=coordinate_confirmation_payload(
                "권한 없는 사용자는 저장할 수 없습니다."
            ),
            headers=ORIGIN,
        )
        assert denied_multi_coordinate_save.status_code == 403

        first_multi_coordinate_save = processor.post(
            f"/api/attachments/{multi_image_attachment_ids[0]}/coordinate-review",
            json=coordinate_confirmation_payload(
                "가나다 어르신 첫 번째 이미지 확인 결과"
            ),
            headers=ORIGIN,
        )
        assert first_multi_coordinate_save.status_code == 201
        after_first_coordinate_save = writer.get(
            f"/api/messages/{multi_image_payload['id']}",
            headers=ORIGIN,
        )
        assert after_first_coordinate_save.status_code == 200
        assert {
            link["resident"]["id"]: link["status"]
            for link in after_first_coordinate_save.json()["message"]["resident_links"]
        } == {
            str(candidate_resident_id): "confirmed",
        }

        second_multi_coordinate_save = processor.post(
            f"/api/attachments/{multi_image_attachment_ids[1]}/coordinate-review",
            json=coordinate_confirmation_payload(
                "두 번째 이미지에는 확인할 이름이 없습니다."
            ),
            headers=ORIGIN,
        )
        assert second_multi_coordinate_save.status_code == 201
        after_all_coordinate_saves = writer.get(
            f"/api/messages/{multi_image_payload['id']}",
            headers=ORIGIN,
        )
        assert after_all_coordinate_saves.status_code == 200
        # A staff-confirmed final text is authoritative immediately. It does
        # not wait for unrelated attachments to be reviewed one by one.
        finalized_links = {
            link["resident"]["id"]: link["status"]
            for link in after_all_coordinate_saves.json()["message"]["resident_links"]
        }
        assert finalized_links == {
            str(candidate_resident_id): "confirmed",
        }
        assert not any(
            link["status"] == "candidate"
            for link in after_all_coordinate_saves.json()["message"]["resident_links"]
        )
        room_messages_after_all_coordinate_saves = writer.get(
            f"/api/rooms/{room_id}/messages",
            headers=ORIGIN,
        )
        assert room_messages_after_all_coordinate_saves.status_code == 200
        room_message_after_all_coordinate_saves = next(
            message
            for message in room_messages_after_all_coordinate_saves.json()
            if message["id"] == multi_image_payload["id"]
        )
        assert {
            link["resident"]["id"]: link["status"]
            for link in room_message_after_all_coordinate_saves["resident_links"]
        } == finalized_links
        assert not any(
            link["status"] == "candidate"
            for link in room_message_after_all_coordinate_saves["resident_links"]
        )
        reopened_first_coordinate_review = processor.get(
            f"/api/attachments/{multi_image_attachment_ids[0]}/coordinate-review",
            headers=ORIGIN,
        )
        assert reopened_first_coordinate_review.status_code == 200
        assert reopened_first_coordinate_review.json()["confirmed_source_text"] == (
            "가나다 어르신 첫 번째 이미지 확인 결과"
        )
        with SessionLocal() as db:
            persisted_link_statuses = {
                link.resident_id: link.status
                for link in db.scalars(
                    select(MessageResidentLink).where(
                        MessageResidentLink.message_id == multi_image_message_id
                    )
                ).all()
            }
            assert persisted_link_statuses == {
                candidate_resident_id: "confirmed",
                resident_2_id: "rejected",
            }
            for attachment_id in multi_image_attachment_ids:
                attachment = db.get(MessageAttachment, attachment_id)
                assert attachment is not None
                extraction = attachment.text_extraction
                assert extraction is not None
                assert {
                    "extracted_text": extraction.extracted_text,
                    "original_extracted_text": extraction.original_extracted_text,
                    "suggested_text": extraction.suggested_text,
                    "reviewed_text": extraction.reviewed_text,
                    "status": extraction.status,
                    "reviewed_by_id": extraction.reviewed_by_id,
                    "reviewed_at": extraction.reviewed_at,
                    "attachment_sha256": attachment.sha256,
                    "attachment_storage_key": attachment.storage_key,
                    "attempt_count": db.scalar(
                        select(func.count(AttachmentTextExtractionAttempt.id)).where(
                            AttachmentTextExtractionAttempt.extraction_id
                            == extraction.id
                        )
                    ),
                } == multi_image_immutable_before[attachment_id]
            coordinate_confirmation_events = db.scalars(
                select(OcrCorrectionEvent).where(
                    OcrCorrectionEvent.attachment_id.in_(multi_image_attachment_ids),
                    OcrCorrectionEvent.coordinate_review_id.is_not(None),
                    OcrCorrectionEvent.confirmed.is_(True),
                )
            ).all()
            assert len(coordinate_confirmation_events) == 2
            assert db.scalar(select(func.count(ConfirmedWorkRecord.id))) == (
                confirmed_record_count_before_coordinate_batch
            )

        batch_requested = processor.post(
            f"/api/messages/{multi_image_payload['id']}/image-text-extractions",
            json={},
            headers=ORIGIN,
        )
        assert batch_requested.status_code == 200, batch_requested.text
        batch_detail = writer.get(
            f"/api/messages/{multi_image_payload['id']}"
        ).json()
        assert [
            attachment["text_extraction"]["status"]
            for attachment in batch_detail["message"]["attachments"]
        ] == ["completed", "completed"]
        batch_review_details = [
            processor.get(
                f"/api/attachments/{attachment['id']}/text-extraction"
            ).json()
            for attachment in batch_detail["message"]["attachments"]
        ]
        assert all(
            attachment["text_extraction"]["original_extracted_text"].startswith(
                "가나타 어르신"
            )
            and attachment["text_extraction"]["suggested_text"].startswith(
                "이름확인필요 어르신"
            )
            and attachment["text_extraction"]["reviewed_text"] is None
            and attachment["text_extraction"]["auto_applied_name_count"] == 0
            and attachment["text_extraction"]["unresolved_name_count"] == 2
            for attachment in batch_review_details
        )
        first_batch_attachment = batch_detail["message"]["attachments"][0]
        confirmed_batch_draft = processor.patch(
            f"/api/attachments/{first_batch_attachment['id']}/text-extraction",
            json={
                "reviewed_text": "가나다 어르신 시험용 손글씨 판독 결과",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert confirmed_batch_draft.status_code == 200, confirmed_batch_draft.text
        confirmed_batch_payload = confirmed_batch_draft.json()["text_extraction"]
        assert confirmed_batch_payload["status"] == "reviewed"
        assert confirmed_batch_payload["reviewed_text"].startswith("가나다 어르신")
        assert confirmed_batch_payload["original_extracted_text"].startswith(
            "가나타 어르신"
        )
        assert len(ocr_calls) == ocr_call_count_before_batch + 2

        repeated_batch = processor.post(
            f"/api/messages/{multi_image_payload['id']}/image-text-extractions",
            json={},
            headers=ORIGIN,
        )
        assert repeated_batch.status_code == 200, repeated_batch.text
        assert len(ocr_calls) == ocr_call_count_before_batch + 2

        forced_batch = processor.post(
            f"/api/messages/{multi_image_payload['id']}/image-text-extractions",
            json={"force": True},
            headers=ORIGIN,
        )
        assert forced_batch.status_code == 200, forced_batch.text
        forced_detail = writer.get(
            f"/api/messages/{multi_image_payload['id']}"
        ).json()
        assert len(ocr_calls) == ocr_call_count_before_batch + 4
        assert [
            attachment["text_extraction"]["attempt_number"]
            for attachment in forced_detail["message"]["attachments"]
        ] == [2, 2]
        assert all(
            attachment["text_extraction"]["previous_attempts"] == []
            for attachment in forced_detail["message"]["attachments"]
        )
        forced_attachment_details = [
            processor.get(
                f"/api/attachments/{attachment['id']}/text-extraction"
            ).json()
            for attachment in forced_detail["message"]["attachments"]
        ]
        assert [
            attachment["text_extraction"]["previous_attempts"][0]["status"]
            for attachment in forced_attachment_details
        ] == ["reviewed", "completed"]
        assert forced_attachment_details[0]["text_extraction"][
            "previous_attempts"
        ][0]["reviewed_text"].startswith("가나다 어르신")

        failed_attachment_id = UUID(multi_image_payload["attachments"][0]["id"])
        with SessionLocal() as db:
            failed_attachment = db.get(MessageAttachment, failed_attachment_id)
            failed_attachment.text_extraction.status = "failed"
            failed_attachment.text_extraction.error_message = "시험용 실패"
            db.commit()
        failed_only_retry = processor.post(
            f"/api/messages/{multi_image_payload['id']}/image-text-extractions",
            json={},
            headers=ORIGIN,
        )
        assert failed_only_retry.status_code == 200, failed_only_retry.text
        retry_detail = writer.get(
            f"/api/messages/{multi_image_payload['id']}"
        ).json()
        assert [
            attachment["text_extraction"]["status"]
            for attachment in retry_detail["message"]["attachments"]
        ] == ["completed", "completed"]
        assert len(ocr_calls) == ocr_call_count_before_batch + 5

        # A room member with explicit workdesk permission must be able to
        # review and retry OCR even when the organization has not assigned a
        # business unit yet. Room membership remains the access boundary.
        room_processor_response = post(
            admin_client,
            "/api/employees",
            {
                "username": "room_record_processor",
                "full_name": "가상 방 처리담당자",
                "password": "RoomProcessPass!234",
                "role": "staff",
                "can_process_records": True,
                "job_code": "social_worker",
                "floor_id": floor_2_id,
                "team_id": processor_team_id,
            },
        )
        assert (
            room_processor_response.status_code == 201
        ), room_processor_response.text
        room_processor = TestClient(app)
        login(room_processor, "room_record_processor", "RoomProcessPass!234")
        password_changed = post(
            room_processor,
            "/api/auth/password",
            {
                "current_password": "RoomProcessPass!234",
                "new_password": "RoomProcessReady!234",
            },
        )
        assert password_changed.status_code == 200, password_changed.text
        assert room_id in {
            room["id"] for room in room_processor.get("/api/rooms").json()
        }

        room_processor_detail = room_processor.get(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction"
        )
        assert room_processor_detail.status_code == 200, room_processor_detail.text
        room_processor_review = room_processor.patch(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "가상 방 처리담당자가 확인한 판독문",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert room_processor_review.status_code == 200, room_processor_review.text
        room_processor_retry = room_processor.post(
            f"/api/messages/{message['id']}/image-text-extractions",
            json={"force": True},
            headers=ORIGIN,
        )
        assert room_processor_retry.status_code == 200, room_processor_retry.text
