from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from .ai_system_schemas import (
    AiGpuInfo,
    AiHardwareStatus,
    AiModeRecommendation,
    AiSystemProviderId,
)


def _cpu_name() -> str:
    name = platform.processor().strip()
    if name:
        return name[:240]
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        try:
            for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.lower().startswith("model name") and ":" in line:
                    value = line.split(":", 1)[1].strip()
                    if value:
                        return value[:240]
        except OSError:
            pass
    return platform.machine() or "확인되지 않은 CPU"


def _system_ram_gb() -> float:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        if page_size > 0 and page_count > 0:
            return round((page_size * page_count) / (1024**3), 2)
    except (AttributeError, OSError, ValueError):
        pass
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.total_physical / (1024**3), 2)
        except (AttributeError, OSError):
            pass
    return 0


def _nvidia_gpus() -> list[AiGpuInfo]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=creation_flags,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    gpus: list[AiGpuInfo] = []
    for line in completed.stdout.splitlines()[:32]:
        if "," not in line:
            continue
        name, memory = (part.strip() for part in line.rsplit(",", 1))
        try:
            vram_gb = round(float(memory) / 1024, 2)
        except ValueError:
            continue
        if name:
            gpus.append(AiGpuInfo(name=name[:160], vram_gb=vram_gb))
    return gpus


def _containerized() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods"))


def _vl_models(models: list[str]) -> list[str]:
    markers = ("-vl", ":vl", "vision", "llava", "omni", "qwen3.6", "gemma4")
    return [model for model in models if any(marker in model.lower() for marker in markers)]


def detect_ai_hardware(
    *,
    ollama_ready: bool,
    ollama_processing_location: str,
    local_models: list[str],
    stt_ready: bool,
    stt_model: str | None,
    external_credentials: list[AiSystemProviderId],
) -> AiHardwareStatus:
    gpus = _nvidia_gpus()
    max_vram = max((gpu.vram_gb for gpu in gpus), default=0)
    location = (
        ollama_processing_location
        if ollama_processing_location in {"local", "internal"}
        else "unconfigured"
    )
    notices: list[str] = []
    if _containerized():
        notices.append(
            "컨테이너 안에서 감지한 값입니다. 호스트 GPU는 passthrough 설정이 없으면 보이지 않습니다."
        )
    if 0 < max_vram < 16:
        notices.append("현재 GPU VRAM은 16GB 미만이므로 대형 로컬 모델을 추천하지 않습니다.")
    if 16 <= max_vram < 24:
        notices.append(
            "VRAM 16GB 이상이어도 Qwen 35B나 Nano Omni NIM 실행을 보장하지 않습니다."
        )
    return AiHardwareStatus(
        containerized=_containerized(),
        os_name=platform.system() or "Unknown",
        os_version=platform.release()[:160],
        architecture=platform.machine()[:80],
        cpu_name=_cpu_name(),
        logical_cpu_count=max(1, os.cpu_count() or 1),
        system_ram_gb=_system_ram_gb(),
        nvidia_gpus=gpus,
        max_nvidia_vram_gb=max_vram,
        ollama_installed_or_reachable=ollama_ready,
        ollama_processing_location=location,
        local_models=local_models[:100],
        local_vl_models=_vl_models(local_models)[:100],
        stt_service_ready=stt_ready,
        stt_model=stt_model,
        external_credentials_configured=external_credentials[:9],
        notices=notices,
    )


def recommend_execution_mode(hardware: AiHardwareStatus) -> AiModeRecommendation:
    if (
        hardware.ollama_installed_or_reachable
        and hardware.ollama_processing_location == "internal"
    ):
        return AiModeRecommendation(
            mode="local_first",
            reasons=[
                "내부 AI 서버가 연결되어 민감자료를 외부로 보내지 않는 경로를 우선할 수 있습니다.",
                "응답이 느린 대형 모델은 규칙 즉시 결과 뒤 비동기 정밀 보강으로 사용합니다.",
            ],
        )
    if hardware.max_nvidia_vram_gb >= 24:
        return AiModeRecommendation(
            mode="local_first",
            reasons=[
                "24GB 이상 NVIDIA GPU가 감지되어 검증된 로컬 모델을 우선할 수 있습니다.",
                "민감자료는 로컬에서 처리하고 비식별 자료만 승인된 API로 보낼 수 있습니다.",
            ],
        )
    if hardware.max_nvidia_vram_gb >= 16:
        return AiModeRecommendation(
            mode="automatic",
            reasons=[
                "VRAM 16GB 이상에서는 경량 로컬 모델과 API를 실제 가용성·지연에 따라 혼합합니다.",
                "Qwen 35B와 Nano Omni NIM의 완전 로컬 실행은 별도 실측 전까지 추천하지 않습니다.",
            ],
        )
    if not hardware.nvidia_gpus:
        return AiModeRecommendation(
            mode="api_first",
            reasons=[
                "전용 NVIDIA GPU가 감지되지 않아 로컬 모델 설치를 강제하지 않습니다.",
                "API 키가 없거나 외부전송이 차단되면 규칙 기반 기본 기능이 즉시 동작합니다.",
            ],
        )
    return AiModeRecommendation(
        mode="api_first",
        reasons=[
            "감지된 GPU VRAM이 16GB 미만이므로 비식별 입력은 검증된 API를 우선합니다.",
            "민감자료는 외부로 보내지 않고 가능한 로컬 경로 또는 규칙 결과를 사용합니다.",
        ],
    )
