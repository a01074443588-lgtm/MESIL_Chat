from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.care_briefing_contract import CareBriefingV1Envelope, CareBriefingV1Input
from app.care_briefing_evaluation import evaluate_care_briefing_case
from app.care_briefing_provider import (
    CareBriefingProvider,
    NvidiaCareBriefingProvider,
    OllamaCareBriefingProvider,
)


DEFAULT_FIXTURE = (
    BACKEND_ROOT / "tests" / "fixtures" / "care_briefing_v1_cases.json"
)
DEFAULT_NVIDIA_ENV_FILE = BACKEND_ROOT.parent / ".env.care-briefing.local"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.6:35b"
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the deidentified care_briefing_v1 comparison suite."
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument(
        "--provider",
        choices=("ollama", "nvidia"),
        default="ollama",
    )
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_NVIDIA_ENV_FILE,
        help="Git-ignored local file containing NVIDIA_API_KEY.",
    )
    parser.add_argument("--api-key-env", default="NVIDIA_API_KEY")
    parser.add_argument(
        "--case-id",
        help="Run one exact deidentified fixture case for a bounded preflight.",
    )
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _read_env_value(path: Path, name: str) -> str:
    """Read one local secret without importing or logging unrelated values."""

    if not path.is_file():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def _select_cases(cases: list[dict], case_id: str | None) -> list[dict]:
    """Select one exact representative case without silently broadening scope."""

    if case_id is None or not case_id.strip():
        return cases
    selected = [case for case in cases if case.get("case_id") == case_id]
    if not selected:
        raise ValueError(f"대표 사례를 찾을 수 없습니다: {case_id}")
    return selected


def _build_provider(
    args: argparse.Namespace,
    diagnostics,
) -> CareBriefingProvider:
    if args.provider == "ollama":
        return OllamaCareBriefingProvider(
            base_url=args.base_url or DEFAULT_OLLAMA_BASE_URL,
            model=args.model or DEFAULT_OLLAMA_MODEL,
            timeout_seconds=args.timeout,
            diagnostics=diagnostics,
        )

    if not args.model or not args.model.strip():
        raise ValueError(
            "NVIDIA 비교에서는 Ultra 또는 Super의 --model을 명시해야 합니다."
        )
    api_key_env = args.api_key_env.strip()
    if not api_key_env:
        raise ValueError("--api-key-env 이름이 비어 있습니다.")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        api_key = _read_env_value(args.env_file, api_key_env)
    if not api_key:
        raise ValueError(
            f"{api_key_env}가 설정되지 않았습니다. "
            f"Git 제외 파일 {args.env_file}에 입력해 주세요."
        )
    return NvidiaCareBriefingProvider(
        base_url=args.base_url or DEFAULT_NVIDIA_BASE_URL,
        model=args.model,
        api_key=api_key,
        timeout_seconds=args.timeout,
        diagnostics=diagnostics,
    )


def _stable_hash(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _percentile_95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, round(0.95 * len(ordered) + 0.5) - 1)
    return ordered[min(index, len(ordered) - 1)]


def _comparable_output(envelope: CareBriefingV1Envelope) -> dict:
    """Keep the validated deidentified result or the standard failure only."""

    return {
        "ok": envelope.ok,
        "result": (
            envelope.result.model_dump(mode="json")
            if envelope.result is not None
            else None
        ),
        "failure": (
            envelope.failure.model_dump(mode="json")
            if envelope.failure is not None
            else None
        ),
    }


def _aggregate(runs: list[dict]) -> dict:
    contract_successes = sum(1 for run in runs if run["contract_ok"])
    elapsed_values = [int(run["elapsed_ms"]) for run in runs]
    score_values = [float(run["measured_score"]) for run in runs]
    failure_counts: dict[str, int] = {}
    for run in runs:
        failure_code = run.get("failure_code")
        if failure_code:
            failure_counts[failure_code] = failure_counts.get(failure_code, 0) + 1
    return {
        "run_count": len(runs),
        "contract_success_count": contract_successes,
        "contract_success_rate": round(
            contract_successes / len(runs), 4
        ) if runs else 0,
        "measured_score_average": round(statistics.mean(score_values), 2)
        if score_values
        else 0,
        "measured_max": 90,
        "elapsed_ms_median": round(statistics.median(elapsed_values))
        if elapsed_values
        else 0,
        "elapsed_ms_p95": _percentile_95(elapsed_values),
        "critical_flaw_run_count": sum(
            1 for run in runs if run["critical_flaws"]
        ),
        "failure_counts": failure_counts,
        "unmeasured_dimensions": [
            "모델 간 처리시간 점수",
            "반복 장애율 점수",
            "운영비용 점수",
        ],
    }


def main() -> int:
    args = _parse_args()
    if args.repeats < 1:
        print("--repeats must be at least 1", file=sys.stderr)
        return 2
    validation_diagnostics: list[dict[str, str]] = []

    def collect_diagnostics(
        _code: str,
        items: list[dict[str, str]],
    ) -> None:
        validation_diagnostics.extend(items)

    try:
        provider = _build_provider(args, collect_diagnostics)
    except ValueError as error:
        print(f"comparison setup error: {error}", file=sys.stderr)
        return 2

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    try:
        cases = _select_cases(fixture["cases"], args.case_id)
    except ValueError as error:
        print(f"fixture selection error: {error}", file=sys.stderr)
        return 2
    runs: list[dict] = []
    total = len(cases) * args.repeats
    completed = 0
    for case in cases:
        input_data = CareBriefingV1Input.model_validate(case["input"])
        input_hash = _stable_hash(input_data.model_dump(mode="json"))
        for repeat in range(1, args.repeats + 1):
            validation_diagnostics.clear()
            envelope = provider.generate(input_data)
            evaluation = evaluate_care_briefing_case(case, envelope)
            comparable_output = _comparable_output(envelope)
            run = evaluation.model_dump(mode="json")
            run.update(
                {
                    "repeat": repeat,
                    "input_hash": input_hash,
                    "output_hash": _stable_hash(comparable_output),
                    "model_output": comparable_output,
                    "validation_diagnostics": list(validation_diagnostics),
                }
            )
            runs.append(run)
            completed += 1
            print(
                f"[{completed}/{total}] {case['case_id']} repeat={repeat} "
                f"ok={evaluation.contract_ok} "
                f"score={evaluation.measured_score}/90 "
                f"elapsed_ms={evaluation.elapsed_ms}",
                file=sys.stderr,
                flush=True,
            )

    report = {
        "report_version": "care_briefing_comparison_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture_name": fixture.get("fixture_name"),
        "fixture_version": fixture.get("version"),
        "provider": provider.provider_name,
        "model": provider.model,
        "selected_case_ids": [case["case_id"] for case in cases],
        "repeats_per_case": args.repeats,
        "aggregate": _aggregate(runs),
        "runs": runs,
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
        print(f"report saved: {args.output}", file=sys.stderr)
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
