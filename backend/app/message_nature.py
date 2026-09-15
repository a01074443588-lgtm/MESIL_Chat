from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


CLASSIFIER_VERSION = "message-nature-v1"
MESSAGE_NATURES = ("chat", "handover", "work_request", "report")


@dataclass(frozen=True)
class MessageNatureDecision:
    message_type: str
    confidence: float
    reason_codes: tuple[str, ...]
    scores: dict[str, int]

    def metadata(
        self,
        *,
        speaker_job_code: str | None,
        speaker_job_name: str | None,
        speaker_position_title: str | None,
        source: str = "automatic_local",
        client_hint: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": CLASSIFIER_VERSION,
            "source": source,
            "message_type": self.message_type,
            "confidence": self.confidence,
            "reason_codes": list(self.reason_codes),
            "speaker_context": {
                "job_code": speaker_job_code,
                "job_name": speaker_job_name,
                "position_title": speaker_position_title,
            },
        }
        if client_hint and client_hint not in {"chat", "notice"}:
            payload["ignored_legacy_client_hint"] = client_hint
        return payload


def _normalized(*values: str | None) -> str:
    return " ".join(
        re.sub(r"\s+", " ", value.strip().lower())
        for value in values
        if value and value.strip()
    )


def _add_score(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    *,
    category: str,
    weight: int,
    reason: str,
) -> None:
    scores[category] += weight
    if reason not in reasons[category]:
        reasons[category].append(reason)


def classify_message_nature(
    body: str,
    *,
    speaker_job_code: str | None = None,
    speaker_job_name: str | None = None,
    speaker_position_title: str | None = None,
    has_attachments: bool = False,
    report_image: bool = False,
) -> MessageNatureDecision:
    """Classify locally so AI/network failure never blocks message delivery."""

    text = _normalized(body)
    speaker_context = _normalized(
        speaker_job_code,
        speaker_job_name,
        speaker_position_title,
    )
    scores = {category: 0 for category in MESSAGE_NATURES if category != "chat"}
    reasons = {category: [] for category in scores}

    patterns: tuple[tuple[str, int, str, str], ...] = (
        (
            "handover",
            5,
            "explicit_handover",
            r"인수인계|인계(?:드|합|해)|다음\s*(?:근무|교대)|야간\s*(?:근무|선생님)|주간\s*(?:근무|선생님)",
        ),
        (
            "handover",
            5,
            "continuing_follow_up",
            r"계속\s*(?:관찰|확인)|추후\s*(?:확인|조치)|이어(?:서)?\s*(?:관찰|확인|처리)|미완료|유의해\s*주",
        ),
        (
            "work_request",
            4,
            "explicit_request",
            r"요청드립니다|부탁드립니다|해\s*주세요|해주시기|바랍니다",
        ),
        (
            "work_request",
            4,
            "requested_action",
            r"(?:확인|조치|준비|연락|처리|전달|챙겨|작성|제출).{0,10}(?:부탁|해\s*주)",
        ),
        (
            "report",
            4,
            "reported_result",
            r"보고(?:드|합|입니)|완료(?:했|되었|됐)|처리(?:했|되었|됐)|확인(?:했|되었|됐)|다녀왔|참여했|진행했|측정했|기록했|관찰(?:했|되었)",
        ),
        (
            "report",
            3,
            "urgent_observation",
            r"낙상|출혈|호흡\s*곤란|의식\s*저하|고열|응급",
        ),
    )
    for category, weight, reason, pattern in patterns:
        if re.search(pattern, text):
            _add_score(
                scores,
                reasons,
                category=category,
                weight=weight,
                reason=reason,
            )

    care_role = re.search(
        r"요양보호|간호|간호조무|물리치료|작업치료|재활",
        speaker_context,
    )
    care_observation = re.search(
        r"기침|체온|혈압|맥박|식사|수분|투약|복약|배변|배뇨|수면|통증|상처|부종|보행|낙상",
        text,
    )
    if care_role and care_observation:
        _add_score(
            scores,
            reasons,
            category="report",
            weight=3,
            reason="care_observation_from_care_role",
        )

    social_work_role = re.search(r"사회복지|복지사|프로그램", speaker_context)
    social_work_event = bool(
        re.search(
            r"보호자|상담|프로그램|계약|급여제공|외출|병원|일정",
            text,
        )
        and re.search(r"예정|완료|참여|진행|변경|취소|확인|방문", text)
    )
    if social_work_role and social_work_event:
        _add_score(
            scores,
            reasons,
            category="report",
            weight=3,
            reason="case_update_from_social_work_role",
        )

    if report_image:
        _add_score(
            scores,
            reasons,
            category="report",
            weight=6,
            reason="report_image_selected",
        )
    elif has_attachments and re.search(r"첨부|사진|파일", text):
        _add_score(
            scores,
            reasons,
            category="report",
            weight=1,
            reason="attachment_context",
        )

    ranked = sorted(
        scores.items(),
        key=lambda item: (
            item[1],
            {"report": 0, "work_request": 1, "handover": 2}[item[0]],
        ),
        reverse=True,
    )
    top_category, top_score = ranked[0]
    second_score = ranked[1][1]
    if top_score < 3:
        weak_signal = top_score > 0
        return MessageNatureDecision(
            message_type="chat",
            confidence=0.52 if weak_signal else 0.64,
            reason_codes=("insufficient_signal",) if weak_signal else ("ordinary_chat",),
            scores=scores,
        )

    margin = top_score - second_score
    confidence = min(0.95, 0.58 + min(top_score, 8) * 0.04 + margin * 0.03)
    return MessageNatureDecision(
        message_type=top_category,
        confidence=round(confidence, 2),
        reason_codes=tuple(reasons[top_category]),
        scores=scores,
    )
