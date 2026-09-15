"""Fail-closed, single-GPU admission using operator-qualified measurements.

This is not a shared GPU scheduler. Profiles must be requalified after any
model, Ollama parallelism/residency configuration or concurrent workload change.
No profile is bundled or enabled by default. Model file size is never VRAM.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .record_text_ai import RecordModelError


class CapacityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_url: str = Field(min_length=1)
    gpu_uuid: str = Field(min_length=1)
    total_mib: int = Field(gt=0)
    model: str = Field(min_length=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_tokens: int = Field(ge=2048, le=32768)
    ollama_version: str = Field(min_length=1)
    # Measured worst-case additional allocation including configured parallel
    # contexts; reserve covers the qualified STT/VL in-flight growth envelope.
    required_free_mib: int = Field(gt=0)
    reserve_mib: int = Field(gt=0)
    max_loaded_models: int = Field(ge=1, le=32)
    allowed_resident_digests: list[str] = Field(max_length=32)


def read_profile(raw: str, *, base: str, model: str, context_tokens: int) -> CapacityProfile:
    try:
        profile = CapacityProfile.model_validate_json(raw)
    except (ValidationError, ValueError, TypeError) as exc:
        raise RecordModelError("gpu_capacity_unverified") from exc
    if (profile.base_url.rstrip("/") != base.rstrip("/") or
            profile.model != model or profile.context_tokens != context_tokens or
            profile.required_free_mib + profile.reserve_mib > profile.total_mib):
        raise RecordModelError("gpu_capacity_unverified")
    return profile


def _measure_free(profile: CapacityProfile) -> tuple[int, float]:
    started = perf_counter()
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=False, timeout=2,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        # Single-GPU qualification only: do not assume where a multi-GPU
        # Ollama server places its model, or pool memory from different GPUs.
        rows = completed.stdout.strip().splitlines()
        if completed.returncode != 0 or len(rows) != 1:
            raise ValueError("unverified telemetry")
        gpu, total, free = (part.strip() for part in rows[0].split(","))
        total_mib, free_mib = int(total), int(free)
        if gpu != profile.gpu_uuid or total_mib != profile.total_mib or not 0 <= free_mib <= total_mib:
            raise ValueError("unverified telemetry")
        return free_mib, started
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RecordModelError("gpu_capacity_unverified") from exc


async def require_capacity(profile: CapacityProfile, *, loaded: list[dict], digest: str | None, version: str | None) -> None:
    if digest != profile.digest or version != profile.ollama_version:
        raise RecordModelError("gpu_capacity_unverified")
    if any(row.get("digest") not in profile.allowed_resident_digests for row in loaded):
        raise RecordModelError("gpu_capacity_unverified")
    if len(loaded) >= profile.max_loaded_models:
        raise RecordModelError("model_prepare_busy")
    free_mib, measured_at = await asyncio.to_thread(_measure_free, profile)
    if not 0 <= perf_counter() - measured_at <= 2.5:
        raise RecordModelError("gpu_capacity_unverified")
    if free_mib < profile.required_free_mib + profile.reserve_mib:
        raise RecordModelError("gpu_memory_insufficient")
