from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


KAKAO_MESSAGE = re.compile(
    r"^\[(?P<sender>[^\]]+)\]\s+\[(?P<time>[^\]]+)\]\s*(?P<body>.*)$"
)
HONORIFIC_NAME = re.compile(r"(?<![가-힣])([가-힣]{2,4})\s*(?:어르신|님)(?![가-힣])")
KOREAN_TERM = re.compile(r"[가-힣]{2,12}")
NON_NAME_WORDS = {
    "보호자",
    "선생",
    "선생님",
    "기사",
    "사회복지사",
    "간호사",
    "간호조무사",
    "요양보호사",
    "아드",
    "아들",
    "딸",
    "둘째아들",
    "첫째아들",
}
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
    "메시지가",
    "삭제되었습니다",
    "일분",
    "정도",
    "같아요",
    "사진",
    "감사합니다",
    "선생님",
    "어르신",
}
LONG_TERM_CARE_TERMS = [
    "확인",
    "교체",
    "기저귀",
    "속패드",
    "입고",
    "관찰됨",
    "상처",
    "멍",
    "병원",
    "진료",
    "보호자",
    "귀가",
    "외출",
    "외박",
    "결석",
    "개별출석",
    "식사",
    "섭취",
    "수분",
    "투약",
    "복약",
    "낙상",
    "욕창",
    "혈압",
    "체온",
    "맥박",
    "목욕",
    "체위변경",
    "대일밴드",
    "공용바지",
    "바지",
    "양말",
    "보행",
    "통증",
    "출혈",
    "배변",
    "배뇨",
    "식사량",
    "복용",
]
INITIAL_CORRECTIONS = {
    # 실버메디컬 샘플에서 담당자가 직접 확인한 반복 판독 오류입니다.
    "락인": "확인",
    "고체": "교체",
    "속때르": "속패드",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="카카오톡 내보내기 파일에서 로컬 OCR 철자 후보를 만듭니다."
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/ocr_lexicon.local.json"),
    )
    parser.add_argument("--max-terms", type=int, default=140)
    return parser.parse_args()


def message_bodies(path: Path) -> list[str]:
    bodies: list[str] = []
    current: list[str] = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        matched = KAKAO_MESSAGE.match(line)
        if matched:
            if current:
                bodies.append("\n".join(current))
            current = [matched.group("body")]
        elif current and line.strip():
            current.append(line.strip())
    if current:
        bodies.append("\n".join(current))
    return bodies


def build_payload(paths: list[Path], max_terms: int) -> dict:
    name_counts: Counter[str] = Counter()
    term_counts: Counter[str] = Counter()
    message_count = 0
    for path in paths:
        for body in message_bodies(path):
            message_count += 1
            for name in HONORIFIC_NAME.findall(body):
                if name not in NON_NAME_WORDS:
                    name_counts[name] += 1
            without_names = HONORIFIC_NAME.sub(" ", body)
            for term in KOREAN_TERM.findall(without_names):
                if term not in STOP_WORDS and term not in NON_NAME_WORDS:
                    term_counts[term] += 1
    resident_candidates = [
        {"term": term, "count": count}
        for term, count in name_counts.most_common(120)
    ]
    organization_terms = [
        {"term": term, "count": count}
        for term, count in term_counts.most_common(max_terms)
    ]
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_count": len(paths),
        "message_count": message_count,
        "resident_name_candidates": resident_candidates,
        "organization_terms": organization_terms,
        "kakao_term_counts": dict(term_counts),
        "chat_term_counts": {},
        "long_term_care_terms": LONG_TERM_CARE_TERMS,
        "corrections": INITIAL_CORRECTIONS,
        # 구버전 로더와의 호환을 위해 단순 목록도 함께 둡니다.
        "names": [item["term"] for item in resident_candidates],
        "terms": [item["term"] for item in organization_terms],
    }


def main() -> None:
    args = parse_args()
    missing = [path for path in args.inputs if not path.is_file()]
    if missing:
        raise SystemExit(f"입력 파일을 찾을 수 없습니다: {len(missing)}개")
    payload = build_payload(args.inputs, args.max_terms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        "로컬 OCR 어휘 생성 완료: "
        f"대화 {payload['message_count']}건, "
        f"이름 후보 {len(payload['resident_name_candidates'])}개, "
        f"기관용어 {len(payload['organization_terms'])}개, "
        f"장기요양 용어 {len(payload['long_term_care_terms'])}개"
    )


if __name__ == "__main__":
    main()
