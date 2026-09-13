"""Compare reviewed audio reports without printing report text or names.

The default mode only measures the already stored raw transcript against the
staff-confirmed review. ``--retranscribe`` repeats the production-safe raw
transcription. ``--context-experiment`` explicitly enables the experimental
long-term-care prompt for comparison. This command never writes to the database.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
import re
import statistics

from sqlalchemy import select

from ..config import settings
from ..database import SessionLocal
from ..domain_lexicon import build_speech_context, canonical_terms
from ..models import (
    AttachmentTextExtraction,
    Message,
    MessageAttachment,
    OcrCorrectionMemory,
    Resident,
    Room,
)
from ..stt import SttError, transcribe_audio


VALID_SERVICES = frozenset({"facility", "daycare", "homecare"})


@dataclass
class MetricSet:
    text_similarity: list[float] = field(default_factory=list)
    resident_name_recall: list[float] = field(default_factory=list)
    domain_term_recall: list[float] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="직원이 확정한 음성보고를 기준으로 문맥 보강 효과를 집계합니다."
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--room-name", default=None)
    parser.add_argument(
        "--retranscribe",
        action="store_true",
        help="원본 음성을 운영 방식(원문 우선)으로 다시 받아씁니다.",
    )
    parser.add_argument(
        "--context-experiment",
        action="store_true",
        help="비교 실험에서만 장기요양 문맥을 음성인식기에 직접 넣습니다.",
    )
    return parser.parse_args()


def normalized(value: str) -> str:
    return "".join(re.findall(r"[가-힣A-Za-z0-9]+", value)).casefold()


def similarity(reference: str, candidate: str) -> float:
    return SequenceMatcher(None, normalized(reference), normalized(candidate)).ratio()


def recall(reference: str, candidate: str, terms: list[str]) -> float:
    expected = {term for term in terms if term and term in reference}
    if not expected:
        return 1.0
    found = {term for term in expected if term in candidate}
    return len(found) / len(expected)


def service_context(message: Message) -> str | None:
    if message.resident is not None and message.resident.service_type in VALID_SERVICES:
        return message.resident.service_type
    scope = message.room.resident_scope if message.room is not None else None
    if scope in VALID_SERVICES:
        return scope
    return "facility" if scope == "floor" else None


def resident_names(db, message: Message) -> list[str]:
    statement = select(Resident.display_name).where(
        Resident.organization_id == message.organization_id,
        Resident.is_active.is_(True),
        Resident.status == "active",
        Resident.service_type.in_(VALID_SERVICES),
    )
    scoped_service = service_context(message)
    if scoped_service is not None:
        statement = statement.where(Resident.service_type == scoped_service)
    return list(db.scalars(statement.order_by(Resident.sort_order, Resident.id)))


def infer_sample_service(
    reference: str,
    direct_service: str | None,
    names_by_service: dict[str, list[str]],
) -> str:
    if direct_service in VALID_SERVICES:
        return direct_service
    matched_services = {
        service
        for service, names in names_by_service.items()
        if any(name and name in reference for name in names)
    }
    return next(iter(matched_services)) if len(matched_services) == 1 else "all"


def learned_terms(db, organization_id) -> list[str]:
    return list(
        db.scalars(
            select(OcrCorrectionMemory.corrected_text)
            .where(OcrCorrectionMemory.organization_id == organization_id)
            .order_by(
                OcrCorrectionMemory.occurrence_count.desc(),
                OcrCorrectionMemory.updated_at.desc(),
            )
            .limit(80)
        )
    )


def record(metrics: MetricSet, reference: str, candidate: str, names: list[str]) -> None:
    metrics.text_similarity.append(similarity(reference, candidate))
    metrics.resident_name_recall.append(recall(reference, candidate, names))
    metrics.domain_term_recall.append(
        recall(reference, candidate, list(canonical_terms()))
    )


def summarize(label: str, metrics: MetricSet) -> str:
    if not metrics.text_similarity:
        return f"{label}_samples=0"
    return " ".join(
        [
            f"{label}_samples={len(metrics.text_similarity)}",
            f"{label}_text_similarity={statistics.fmean(metrics.text_similarity):.3f}",
            f"{label}_resident_name_recall={statistics.fmean(metrics.resident_name_recall):.3f}",
            f"{label}_domain_term_recall={statistics.fmean(metrics.domain_term_recall):.3f}",
        ]
    )


def main() -> None:
    args = parse_args()
    limit = max(1, min(20, args.limit))
    baseline = MetricSet()
    contextual = MetricSet()
    baseline_by_service: dict[str, MetricSet] = defaultdict(MetricSet)
    contextual_by_service: dict[str, MetricSet] = defaultdict(MetricSet)
    missing_files = 0
    retranscribe_failures = 0
    upload_dir = Path(settings.upload_dir).resolve()

    with SessionLocal() as db:
        statement = (
            select(AttachmentTextExtraction)
            .join(MessageAttachment)
            .join(Message)
            .join(Room)
            .where(
                AttachmentTextExtraction.status == "reviewed",
                AttachmentTextExtraction.reviewed_text.is_not(None),
                MessageAttachment.mime_type.like("audio/%"),
            )
            .order_by(
                AttachmentTextExtraction.reviewed_at.desc(),
                AttachmentTextExtraction.id,
            )
        )
        if args.room_name:
            statement = statement.where(Room.name == args.room_name)
        extractions = list(db.scalars(statement.limit(limit)))

        for extraction in extractions:
            attachment = extraction.attachment
            message = attachment.message
            reference = (extraction.reviewed_text or "").strip()
            raw = (
                extraction.original_extracted_text
                or extraction.extracted_text
                or ""
            ).strip()
            if not reference or not raw:
                continue
            names = resident_names(db, message)
            names_by_service = {
                service: list(
                    db.scalars(
                        select(Resident.display_name).where(
                            Resident.organization_id == message.organization_id,
                            Resident.is_active.is_(True),
                            Resident.status == "active",
                            Resident.service_type == service,
                        )
                    )
                )
                for service in sorted(VALID_SERVICES)
            }
            sample_service = infer_sample_service(
                reference,
                service_context(message),
                names_by_service,
            )
            record(baseline, reference, raw, names)
            record(baseline_by_service[sample_service], reference, raw, names)

            if not args.retranscribe:
                continue
            source_path = (upload_dir / attachment.storage_key).resolve()
            if source_path.parent != upload_dir or not source_path.is_file():
                missing_files += 1
                continue
            try:
                transcribe_options = {}
                if args.context_experiment:
                    context = build_speech_context(
                        resident_names=names,
                        organization_terms=learned_terms(
                            db,
                            message.organization_id,
                        ),
                        service_context=service_context(message),
                    )
                    transcribe_options = {
                        "initial_prompt": context.initial_prompt,
                        "hotwords": context.hotwords,
                    }
                candidate = transcribe_audio(
                    source_path,
                    mime_type=attachment.mime_type,
                    **transcribe_options,
                )
            except SttError:
                retranscribe_failures += 1
                continue
            record(contextual, reference, candidate, names)
            record(
                contextual_by_service[sample_service],
                reference,
                candidate,
                names,
            )

    print(
        " ".join(
            [
                "mode="
                + (
                    "context_experiment"
                    if args.context_experiment
                    else "retranscribe"
                    if args.retranscribe
                    else "baseline"
                ),
                f"eligible={len(extractions)}",
                f"missing_files={missing_files}",
                f"retranscribe_failures={retranscribe_failures}",
                summarize("baseline", baseline),
                summarize("contextual", contextual),
                *(
                    summarize(
                        f"baseline_{service}",
                        baseline_by_service[service],
                    )
                    for service in ("facility", "daycare", "homecare", "all")
                ),
                *(
                    summarize(
                        f"contextual_{service}",
                        contextual_by_service[service],
                    )
                    for service in ("facility", "daycare", "homecare", "all")
                ),
            ]
        )
    )


if __name__ == "__main__":
    main()
