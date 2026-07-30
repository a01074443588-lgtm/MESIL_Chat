"""현재 채팅 DB의 단어빈도를 로컬 OCR 기관사전에 지연 없이 합칩니다."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Message, Resident, Staff, User  # noqa: E402


KOREAN_TERM = re.compile(r"[가-힣]{2,12}")
HONORIFIC_NAME = re.compile(r"(?<![가-힣])([가-힣]{2,4})\s*(?:어르신|님)(?![가-힣])")
STOP_WORDS = {
    "그냥",
    "그리고",
    "그러면",
    "입니다",
    "있습니다",
    "없습니다",
    "합니다",
    "했습니다",
    "해주세요",
    "드립니다",
    "오늘",
    "내일",
    "지금",
    "금일",
    "오전",
    "오후",
    "월요일",
    "화요일",
    "수요일",
    "목요일",
    "금요일",
    "토요일",
    "일요일",
    "일분",
    "정도",
    "같아요",
    "사진",
    "감사합니다",
    "선생님",
    "어르신",
    "메시지가",
    "삭제되었습니다",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "현재 SMCODI_CHAT 대화를 다시 집계해 로컬 OCR 기관사전을 갱신합니다. "
            "메시지 전송 경로에서는 실행되지 않습니다."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(settings.ocr_lexicon_path),
    )
    parser.add_argument("--max-terms", type=int, default=240)
    return parser.parse_args()


def _counts_from_payload(payload: dict, field_name: str) -> Counter[str]:
    raw = payload.get(field_name, {})
    if isinstance(raw, dict):
        return Counter(
            {
                str(term): int(count)
                for term, count in raw.items()
                if str(term).strip() and int(count) > 0
            }
        )
    return Counter()


def main() -> None:
    args = parse_args()
    if args.output.is_file():
        payload = json.loads(args.output.read_text(encoding="utf-8"))
    else:
        payload = {
            "schema_version": 2,
            "resident_name_candidates": [],
            "long_term_care_terms": [],
            "corrections": {},
            "names": [],
            "terms": [],
        }
    with SessionLocal() as db:
        messages = db.scalars(select(Message.body)).all()
        protected_names = {
            name
            for name in [
                *db.scalars(select(Resident.display_name)).all(),
                *db.scalars(select(Staff.display_name)).all(),
                *db.scalars(select(User.display_name)).all(),
            ]
            if name
        }

    counts: Counter[str] = Counter()
    for body in messages:
        without_names = HONORIFIC_NAME.sub(" ", body or "")
        for term in KOREAN_TERM.findall(without_names):
            if term in STOP_WORDS or term in protected_names:
                continue
            counts[term] += 1

    kakao_counts = _counts_from_payload(payload, "kakao_term_counts")
    if not kakao_counts:
        kakao_counts.update(
            {
                str(item.get("term")): int(item.get("count", 1))
                for item in payload.get("organization_terms", [])
                if isinstance(item, dict) and item.get("term")
            }
        )
    combined = kakao_counts + counts
    organization_terms = [
        {"term": term, "count": count}
        for term, count in combined.most_common(args.max_terms)
    ]
    payload.update(
        {
            "schema_version": 2,
            "chat_updated_at": datetime.now(timezone.utc).isoformat(),
            "chat_message_count": len(messages),
            "chat_term_counts": dict(counts),
            "organization_terms": organization_terms,
            "terms": [item["term"] for item in organization_terms],
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        "채팅 기관용어 갱신 완료: "
        f"대화 {len(messages)}건, 채팅용어 {len(counts)}개, "
        f"통합 상위용어 {len(organization_terms)}개"
    )


if __name__ == "__main__":
    main()
