from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app import local_ai  # noqa: E402


CASES = [
    {
        "name": "식사량_관찰",
        "source": (
            "시설(가명)001 어르신이 점심을 절반 정도 드셨습니다. "
            "평소보다 적게 드셔서 14시에 다시 상태를 확인하기로 했습니다."
        ),
        "draft": {
            "corrected_text": (
                "시설(가명)001 어르신이 점심을 절반 정도 드셨습니다. "
                "평소보다 적게 드셔서 14시에 다시 상태를 확인하기로 했습니다."
            ),
            "summary": "점심 식사량 감소를 관찰하고 14시에 다시 확인할 예정입니다.",
            "classification": "nutrition",
            "risk_level": "medium",
            "target_roles": ["caregiver", "nurse"],
            "document_types": ["care_service_record", "nursing_log"],
            "keywords": ["점심", "식사량", "14시"],
        },
        "required_terms": ["시설(가명)001", "점심", "절반", "14시"],
        "forbidden_terms": ["낙상", "욕창", "투약", "병원"],
    },
    {
        "name": "이동_안전_관찰",
        "source": (
            "시설(가명)002 어르신이 침대에서 일어나실 때 잠시 휘청하셨으나 "
            "넘어지지는 않았습니다. 요양보호사가 부축하여 의자에 앉으셨습니다."
        ),
        "draft": {
            "corrected_text": (
                "시설(가명)002 어르신이 침대에서 일어나실 때 잠시 휘청하셨으나 "
                "넘어지지는 않았습니다. 요양보호사가 부축하여 의자에 앉으셨습니다."
            ),
            "summary": "기립 중 휘청거림을 관찰하고 부축하여 안전하게 앉았습니다.",
            "classification": "safety",
            "risk_level": "medium",
            "target_roles": ["caregiver", "nurse"],
            "document_types": [
                "care_service_record",
                "integrated_assessment",
                "nursing_log",
            ],
            "keywords": ["기립", "휘청거림", "부축"],
        },
        "required_terms": ["시설(가명)002", "휘청", "넘어지지는 않았", "부축"],
        "forbidden_terms": ["골절", "출혈", "병원", "투약"],
    },
    {
        "name": "다중_어르신_건강",
        "source": (
            "시설(가명)003 어르신은 오전 체온이 37.8도로 간호조무사에게 알렸습니다. "
            "시설(가명)004 어르신은 아침 식사를 모두 드셨습니다."
        ),
        "draft": {
            "corrected_text": (
                "시설(가명)003 어르신은 오전 체온이 37.8도로 간호조무사에게 알렸습니다. "
                "시설(가명)004 어르신은 아침 식사를 모두 드셨습니다."
            ),
            "summary": (
                "시설(가명)003 어르신의 체온 37.8도를 간호조무사에게 알렸고, "
                "시설(가명)004 어르신은 아침 식사를 모두 섭취했습니다."
            ),
            "classification": "health",
            "risk_level": "medium",
            "target_roles": ["caregiver", "nurse"],
            "document_types": ["care_service_record", "nursing_log"],
            "keywords": ["체온", "37.8도", "아침 식사"],
        },
        "required_terms": [
            "시설(가명)003",
            "37.8도",
            "간호조무사",
            "시설(가명)004",
            "모두",
        ],
        "forbidden_terms": ["해열제", "병원", "감염", "투약"],
    },
    {
        "name": "보호자_상담",
        "source": (
            "시설(가명)005 어르신 보호자와 통화했습니다. "
            "보호자가 내일 미끄럼 방지 실내화를 가져오기로 했습니다."
        ),
        "draft": {
            "corrected_text": (
                "시설(가명)005 어르신 보호자와 통화했습니다. "
                "보호자가 내일 미끄럼 방지 실내화를 가져오기로 했습니다."
            ),
            "summary": "보호자와 통화하여 내일 미끄럼 방지 실내화를 준비하기로 했습니다.",
            "classification": "consultation",
            "risk_level": "low",
            "target_roles": ["caregiver", "social_worker"],
            "document_types": ["consultation_log", "care_service_record"],
            "keywords": ["보호자 통화", "미끄럼 방지 실내화", "내일"],
        },
        "required_terms": ["시설(가명)005", "보호자", "내일", "미끄럼 방지 실내화"],
        "forbidden_terms": ["낙상", "구매", "비용", "병원"],
    },
]


def run_case(model: str, case: dict, timeout_seconds: int) -> dict:
    local_ai.settings.ai_review_base_url = "http://127.0.0.1:11434"
    local_ai.settings.ai_review_timeout_seconds = timeout_seconds
    payload = local_ai._user_payload(
        source_snapshot={
            "body": case["source"],
            "resident_names": [term for term in case["required_terms"] if "(가명)" in term],
            "is_test_data": True,
        },
        current_draft=case["draft"],
        lexicon_context={"resident_candidates": ["시설(가명)001", "시설(가명)002"]},
    )
    started = perf_counter()
    draft = local_ai._review_with_ollama(model=model, user_payload=payload)
    elapsed_seconds = round(perf_counter() - started, 2)
    combined = f"{draft['corrected_text']}\n{draft['summary']}"
    missing_terms = [term for term in case["required_terms"] if term not in combined]
    invented_terms = [term for term in case["forbidden_terms"] if term in combined]
    return {
        "case": case["name"],
        "elapsed_seconds": elapsed_seconds,
        "json_schema_valid": True,
        "required_terms_preserved": not missing_terms,
        "missing_terms": missing_terms,
        "invented_terms": invented_terms,
        "classification_matches_draft": (
            draft["classification"] == case["draft"]["classification"]
        ),
        "risk_level_matches_draft": (
            draft["risk_level"] == case["draft"]["risk_level"]
        ),
        "target_roles_preserved": set(case["draft"]["target_roles"]).issubset(
            draft["target_roles"]
        ),
        "document_types_preserved": set(case["draft"]["document_types"]).issubset(
            draft["document_types"]
        ),
        "draft": draft,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3.6:35b")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    results: list[dict] = []
    for case in CASES:
        try:
            results.append(run_case(args.model, case, args.timeout))
        except Exception as exc:  # benchmark must preserve the failure as evidence
            results.append(
                {
                    "case": case["name"],
                    "json_schema_valid": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    successful = [result for result in results if result.get("json_schema_valid")]
    summary = {
        "model": args.model,
        "case_count": len(results),
        "success_count": len(successful),
        "average_seconds": (
            round(
                sum(result["elapsed_seconds"] for result in successful)
                / len(successful),
                2,
            )
            if successful
            else None
        ),
        "all_required_terms_preserved": bool(successful)
        and all(result["required_terms_preserved"] for result in successful),
        "no_invented_terms": bool(successful)
        and all(not result["invented_terms"] for result in successful),
        "classification_match_count": sum(
            bool(result["classification_matches_draft"]) for result in successful
        ),
        "risk_match_count": sum(
            bool(result["risk_level_matches_draft"]) for result in successful
        ),
        "target_roles_preserved_count": sum(
            bool(result["target_roles_preserved"]) for result in successful
        ),
        "document_types_preserved_count": sum(
            bool(result["document_types_preserved"]) for result in successful
        ),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if len(successful) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
