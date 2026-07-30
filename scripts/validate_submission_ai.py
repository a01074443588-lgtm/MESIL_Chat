"""제출 시연용 가명 업무사례 4건을 실제 AI 체인으로 최종 검증합니다."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select


def _configure_import_path() -> None:
    cwd = Path.cwd()
    candidates = (
        cwd / "backend",
        cwd,
        Path(__file__).resolve().parents[1] / "backend",
    )
    for candidate in candidates:
        if (candidate / "app").is_dir():
            sys.path.insert(0, str(candidate))
            return


_configure_import_path()

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.local_ai import LocalAiError, refine_record_draft  # noqa: E402
from app.main import (  # noqa: E402
    _ocr_correction_pairs,
    _work_item_ai_snapshot,
    _work_item_comments,
)
from app.models import Resident, User, WorkItem, utcnow  # noqa: E402
from app.ocr import get_ai_lexicon_context  # noqa: E402
from app.prototype_ai import build_prototype_suggestion  # noqa: E402
from app.schemas import RecordDraft  # noqa: E402
from app.services import record_audit  # noqa: E402


CONFIRMATION = "VALIDATE_SUBMISSION_AI"
EXPECTED_CASES = 4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="가명 제출 시연사례 4건 실제 AI 최종 검증"
    )
    parser.add_argument(
        "--apply",
        metavar="CONFIRMATION",
        help=f"실제 AI 호출 확인 문구: {CONFIRMATION}",
    )
    parser.add_argument(
        "--resident-name",
        help="한 사례만 다시 검증할 때 사용할 가명 어르신 표시명",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    applying = args.apply is not None
    if applying and args.apply != CONFIRMATION:
        print(f"확인 문구가 올바르지 않습니다. 필요한 값: {CONFIRMATION}")
        return 2

    failures: list[str] = []
    with SessionLocal() as db:
        query = (
            select(WorkItem)
            .where(
                WorkItem.is_test_data.is_(True),
                WorkItem.confirmed_at.is_(None),
            )
            .order_by(WorkItem.created_at)
        )
        expected_cases = EXPECTED_CASES
        if args.resident_name:
            query = query.join(
                Resident,
                WorkItem.resident_id == Resident.id,
            ).where(Resident.display_name == args.resident_name)
            expected_cases = 1
        items = db.scalars(query).all()
        if len(items) != expected_cases:
            raise RuntimeError(
                "AI 검증 대상이 예상과 다릅니다. "
                f"예상={expected_cases}, 실제={len(items)}"
            )
        print(f"AI 검증 대상: {len(items)}건")
        for index, item in enumerate(items, start=1):
            snapshot = _work_item_ai_snapshot(db, item)
            room_name = str(snapshot.get("room_name") or "채팅방")
            resident_name = str(snapshot.get("resident_name") or "어르신")
            print(f"[{index}/{len(items)}] {room_name} · {resident_name}", flush=True)
            if not applying:
                continue

            # 최종 검증을 반복할 때 이전 AI 오분류를 다시 안전기준으로 삼지 않는다.
            # 현재 코드의 결정론적 장기요양 분류 초안에서 매번 새로 시작한다.
            current_draft = RecordDraft.model_validate(
                build_prototype_suggestion(snapshot)
            ).model_dump(mode="json")
            correction_pairs = _ocr_correction_pairs(db, item.organization_id)
            try:
                result = refine_record_draft(
                    source_snapshot=snapshot,
                    current_draft=current_draft,
                    lexicon_context=get_ai_lexicon_context(
                        str(snapshot.get("body", "")),
                        correction_pairs=correction_pairs,
                    ),
                    external_allowed=True,
                )
                refined = RecordDraft.model_validate(result.draft)
            except (LocalAiError, ValidationError) as exc:
                db.rollback()
                failures.append(f"{room_name} · {resident_name}: {str(exc)[:240]}")
                print(f"  실패: {str(exc)[:240]}", flush=True)
                continue

            admin_username = settings.bootstrap_admin_username
            if not admin_username:
                raise RuntimeError("환경변수에 AI 검증 담당 계정명이 설정되지 않았습니다.")
            processor = db.scalar(
                select(User).where(User.username == admin_username)
            )
            if processor is None:
                raise RuntimeError("AI 검증 담당 관리자 계정을 찾을 수 없습니다.")
            item.ai_state = "ai_reviewed"
            item.ai_payload = {
                **refined.model_dump(mode="json"),
                "source_comment_ids": [
                    str(comment.id) for comment in _work_item_comments(db, item)
                ],
                "_review_meta": {
                    "provider": result.provider,
                    "model": result.model,
                    "elapsed_ms": result.elapsed_ms,
                    "attempts": result.attempts,
                },
            }
            item.ai_generator = f"{result.provider}:{result.model}"[:80]
            item.ai_generated_at = utcnow()
            item.document_types = refined.document_types
            item.status = "in_review"
            item.handled_by_id = processor.id
            record_audit(
                db,
                actor_id=processor.id,
                action="work_item.ai_reviewed",
                target_type="work_item",
                target_id=item.id,
                details={
                    "provider": result.provider,
                    "model": result.model,
                    "elapsed_ms": result.elapsed_ms,
                    "attempts": result.attempts,
                    "submission_validation": True,
                },
            )
            db.commit()
            print(
                "  성공: "
                f"{result.provider} · {result.model} · {result.elapsed_ms}ms · "
                f"{refined.classification} · {refined.risk_level} · "
                f"{','.join(refined.document_types)}",
                flush=True,
            )

    if failures:
        print("\n실패 사례")
        for failure in failures:
            print(f"- {failure}")
        return 1
    if not applying:
        print("모의 실행 완료: 실제 AI는 호출하지 않았습니다.")
    else:
        print(f"가명 사례 {expected_cases}건의 실제 AI 검증이 완료되었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
