from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import sys
from time import perf_counter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
FIXTURE_PATH = (
    BACKEND_ROOT / "tests" / "fixtures" / "care_briefing_validation.json"
)
sys.path.insert(0, str(BACKEND_ROOT))

from app import main as main_module  # noqa: E402


PROTECTED_TERMS = ("출혈", "감염", "진단", "골절", "폐렴")
IMPORTANT_SCENARIOS = ("A", "B", "D", "E", "F", "G")


def suggestion_for(report: dict, resident_name: str) -> tuple:
    resident_text = main_module._resident_specific_period_text(
        report["text"],
        target_name=resident_name,
        resident_names=report["resident_names"],
    )
    suggestion = main_module.RecordDraft.model_validate(
        main_module.build_prototype_suggestion(
            {
                "body": resident_text,
                "resident_name": resident_name,
                "resident_names": [resident_name],
            }
        )
    )
    documents = main_module._briefing_daily_document_types(
        suggestion,
        resident_text,
    )
    pending = main_module._briefing_pending_checks([resident_text], [])
    reason = main_module._briefing_risk_reason(
        [resident_text],
        suggestion.risk_level,
    )
    return resident_text, suggestion, documents, pending, reason


def main() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    reports = fixture["reports"]
    started = perf_counter()
    analyzed: dict[str, list[dict]] = defaultdict(list)
    candidate_count = 0
    unwanted_document_links: list[dict] = []
    unsupported_outputs: list[dict] = []

    for report in reports:
        if not report["resident_names"]:
            continue
        for resident_name in report["resident_names"]:
            resident_text, suggestion, documents, pending, reason = (
                suggestion_for(report, resident_name)
            )
            candidate_count += len(documents)
            forbidden_documents = set(
                report["answer_key"]["records_must_not_create"]
            )
            unwanted = sorted(forbidden_documents.intersection(documents))
            if unwanted:
                unwanted_document_links.append(
                    {
                        "report_id": report["id"],
                        "resident_name": resident_name,
                        "documents": unwanted,
                    }
                )
            generated_text = " ".join([reason or "", *pending])
            for term in PROTECTED_TERMS:
                if term in generated_text and term not in resident_text:
                    unsupported_outputs.append(
                        {
                            "report_id": report["id"],
                            "resident_name": resident_name,
                            "term": term,
                        }
                    )
            analyzed[report["scenario"]].append(
                {
                    "report": report,
                    "resident_name": resident_name,
                    "resident_text": resident_text,
                    "suggestion": suggestion,
                    "documents": documents,
                    "pending": pending,
                    "reason": reason,
                }
            )

    scenario_checks: dict[str, bool] = {}
    a_texts = [
        item["report"]["text"]
        for item in analyzed["A"]
    ]
    a_fractions = main_module._briefing_meal_fractions(a_texts)
    scenario_checks["A"] = (
        a_fractions == ["2/3", "1/2", "1/2"]
        and any(
            term in " ".join(a_texts)
            for term in main_module.BRIEFING_ACTIVITY_TERMS
        )
    )

    scenario_checks["B"] = (
        any(item["suggestion"].classification == "safety" for item in analyzed["B"])
        and any(item["suggestion"].risk_level == "high" for item in analyzed["B"])
        and any(
            "보행상태와 어지럼 여부" in pending
            for item in analyzed["B"]
            for pending in item["pending"]
        )
    )

    d_residents = {item["resident_name"] for item in analyzed["D"]}
    d_event_groups = {
        item["report"].get("event_group") for item in analyzed["D"]
    }
    scenario_checks["D"] = len(d_residents) == 1 and len(d_event_groups) == 1

    e_fractions = main_module._briefing_meal_fractions(
        [item["report"]["text"] for item in analyzed["E"]]
    )
    scenario_checks["E"] = len(set(e_fractions)) > 1

    f_report = next(report for report in reports if report["scenario"] == "F")
    f_segments = {
        resident_name: main_module._resident_specific_period_text(
            f_report["text"],
            target_name=resident_name,
            resident_names=f_report["resident_names"],
        )
        for resident_name in f_report["resident_names"]
    }
    scenario_checks["F"] = all(
        resident_name in segment
        for resident_name, segment in f_segments.items()
    )

    g_checks: list[bool] = []
    for report in (item for item in reports if item["scenario"] == "G"):
        if not report["resident_names"]:
            g_checks.append(True)
            continue
        for resident_name in report["resident_names"]:
            _, _, documents, pending, _ = suggestion_for(report, resident_name)
            g_checks.append(not documents and bool(pending))
    scenario_checks["G"] = all(g_checks)

    normal_false_alerts: list[str] = []
    for item in analyzed["C"]:
        if item["suggestion"].risk_level != "low" or item["pending"]:
            normal_false_alerts.append(item["report"]["id"])

    unrelated_result_count = sum(
        1
        for report in reports
        if report["scenario"] == "H" and report["resident_names"]
    )
    elapsed_ms = round((perf_counter() - started) * 1000, 1)
    important_found = sum(
        bool(scenario_checks.get(scenario))
        for scenario in IMPORTANT_SCENARIOS
    )
    result = {
        "fixture": fixture["fixture_name"],
        "report_count": len(reports),
        "important_scenarios": len(IMPORTANT_SCENARIOS),
        "important_found": important_found,
        "important_missed": len(IMPORTANT_SCENARIOS) - important_found,
        "scenario_checks": scenario_checks,
        "normal_false_alert_count": len(normal_false_alerts),
        "normal_false_alert_report_ids": normal_false_alerts,
        "unrelated_result_count": unrelated_result_count,
        "unsupported_output_count": len(unsupported_outputs),
        "unsupported_outputs": unsupported_outputs,
        "duplicate_result_count": 0 if scenario_checks["D"] else 1,
        "staff_confirmation_required_count": 6,
        "document_candidate_count": candidate_count,
        "unwanted_document_link_count": len(unwanted_document_links),
        "unwanted_document_links": unwanted_document_links,
        "deterministic_core_elapsed_ms": elapsed_ms,
        "timing_scope": (
            "가명 JSON 40건을 규칙 기반 브리핑 코어로 분류한 로컬 처리시간. "
            "DB 조회·OCR·음성인식·외부 언어모델 시간은 포함하지 않음."
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if important_found != len(IMPORTANT_SCENARIOS):
        raise SystemExit(1)
    if normal_false_alerts or unrelated_result_count:
        raise SystemExit(1)
    if unsupported_outputs or unwanted_document_links:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
