from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


AiExecutionMode = Literal["automatic", "api_first", "local_first", "fully_local"]
AiDataClassification = Literal[
    "general_non_sensitive",
    "deidentified_confirmed",
    "possibly_sensitive",
    "personal_or_care_record",
]
AiSystemProviderId = Literal[
    "rules",
    "ollama",
    "ollama_cloud",
    "nvidia",
    "openai",
    "gemini",
    "anthropic",
    "openai_compatible",
    "local_stt",
]
AiCapability = Literal[
    "text",
    "image",
    "audio",
    "video",
    "structured_output",
    "speech_to_text",
]
AiProcessingLocation = Literal["rules", "local", "internal", "external", "unconfigured"]
AiRoutePath = Literal["rules", "local", "internal", "external", "none"]
AiFailureCode = Literal[
    "key_unconfigured",
    "authentication_failed",
    "billing_required",
    "free_tier_exhausted",
    "quota_exceeded",
    "rate_limited",
    "timeout",
    "provider_unavailable",
    "unsupported_input",
    "contract_validation_failed",
    "privacy_blocked",
    "local_service_unavailable",
    "invalid_response",
    "time_budget_exhausted",
    "attempt_limit_exhausted",
]


class AiGpuInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    vram_gb: float = Field(ge=0, le=1024)


class AiHardwareStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detection_scope: Literal["application_runtime"] = "application_runtime"
    containerized: bool
    os_name: str = Field(min_length=1, max_length=80)
    os_version: str = Field(default="", max_length=160)
    architecture: str = Field(default="", max_length=80)
    cpu_name: str = Field(min_length=1, max_length=240)
    logical_cpu_count: int = Field(ge=1, le=4096)
    system_ram_gb: float = Field(ge=0, le=65536)
    nvidia_gpus: list[AiGpuInfo] = Field(default_factory=list, max_length=32)
    max_nvidia_vram_gb: float = Field(default=0, ge=0, le=1024)
    ollama_installed_or_reachable: bool
    ollama_processing_location: Literal["local", "internal", "unconfigured"]
    local_models: list[str] = Field(default_factory=list, max_length=100)
    local_vl_models: list[str] = Field(default_factory=list, max_length=100)
    stt_service_ready: bool
    stt_model: str | None = Field(default=None, max_length=160)
    external_credentials_configured: list[AiSystemProviderId] = Field(
        default_factory=list,
        max_length=9,
    )
    notices: list[str] = Field(default_factory=list, max_length=20)


class AiModeRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AiExecutionMode
    reasons: list[str] = Field(min_length=1, max_length=10)
    local_model_install_required: Literal[False] = False


class AiProviderPublicStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AiSystemProviderId
    display_name: str = Field(min_length=1, max_length=80)
    api_key_env_var: str | None = Field(default=None, max_length=80)
    enabled: bool
    adapter_available: bool
    credential_configured: bool
    endpoint_configured: bool
    endpoint_scope: Literal[
        "rules",
        "local",
        "internal",
        "external",
        "invalid",
        "unconfigured",
    ]
    processing_location: AiProcessingLocation
    connection_state: Literal[
        "ready",
        "configured_unverified",
        "unconfigured",
        "unavailable",
    ]
    configured_model: str | None = Field(default=None, max_length=200)
    models: list[str] = Field(default_factory=list, max_length=100)
    capabilities: list[AiCapability] = Field(default_factory=list, max_length=10)
    timeout_seconds: int = Field(ge=1, le=900)
    last_latency_ms: int | None = Field(default=None, ge=0)
    last_error_code: AiFailureCode | None = None
    last_error_message: str | None = Field(default=None, max_length=300)
    structured_contract_verified: bool = False
    external_transmission_default_allowed: Literal[False] = False
    secret_value_included: Literal[False] = False


class AiModelRoleSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AiSystemProviderId
    model: str = Field(min_length=1, max_length=200)


class AiModelRoles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: AiModelRoleSelection
    vision: AiModelRoleSelection | None = None
    stt: AiModelRoleSelection | None = None
    precision: AiModelRoleSelection


class AiRoleModelOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AiSystemProviderId
    model: str = Field(min_length=1, max_length=200)
    processing_location: AiProcessingLocation
    external_transmission: bool
    cost_notice: Literal["no_extra_cost", "cost_possible"]


class AiRoleModelOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: list[AiRoleModelOption] = Field(default_factory=list, max_length=9)
    vision: list[AiRoleModelOption] = Field(default_factory=list, max_length=9)
    stt: list[AiRoleModelOption] = Field(default_factory=list, max_length=9)
    precision: list[AiRoleModelOption] = Field(default_factory=list, max_length=9)


class AiProviderSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    timeout_seconds: int | None = Field(default=None, ge=1, le=900)

    @field_validator("base_url", "model")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AiCentralModels(BaseModel):
    """Shared defaults for record text features; legacy role choices stay intact."""

    model_config = ConfigDict(extra="forbid")
    default_provider: AiSystemProviderId = "ollama"
    inheritance_version: Literal[1, 2] = 1
    execution_environment: Literal["local", "internal", "external", "rules"] = "internal"
    text_default_model: str | None = Field(default=None, max_length=200)
    text_fallback_model: str | None = Field(default=None, max_length=200)
    text_fallback_candidate: str | None = Field(default=None, max_length=200)
    fallback_qualified: bool = False
    vision_default_model: str | None = Field(default=None, max_length=200)
    vision_fallback_model: str | None = Field(default=None, max_length=200)
    feature_overrides: dict[Literal["search_summary", "care_record_question", "document_text", "precision", "image_reading", "stt"], str | AiModelRoleSelection] = Field(default_factory=dict)
    image_verification_bindings: list[str] = Field(default_factory=list, max_length=50)
    base_url: str | None = Field(default=None, max_length=500)
    timeout_seconds: int = Field(default=15, ge=2, le=30)
    context_tokens: int = Field(default=8192, ge=2048, le=32768)
    max_input_chars: int = Field(default=18000, ge=2000, le=60000)
    real_record_logging_verified: bool = False

    @model_validator(mode="after")
    def require_qualified_fallback(self):
        if self.text_fallback_model and not self.fallback_qualified:
            raise ValueError("검증되지 않은 모델은 대체 후보로만 등록할 수 있습니다.")
        models = [self.text_default_model, self.text_fallback_model, self.vision_default_model, self.vision_fallback_model]
        for value in self.feature_overrides.values():
            models.append(value if isinstance(value, str) else value.model)
        for model in models:
            if model is not None and not model.strip():
                raise ValueError("로컬 텍스트 모델의 정확한 태그를 입력해 주세요.")
        if self.default_provider == "ollama" and any(model and "cloud" in model.lower() for model in [self.text_default_model, self.text_fallback_model, self.vision_default_model, self.vision_fallback_model]):
            raise ValueError("로컬 연결에는 클라우드 모델을 지정할 수 없습니다.")
        return self


class AiCentralConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: Literal["local", "internal", "external", "rules"]
    provider: AiSystemProviderId = "ollama"
    base_url: str | None = Field(default=None, max_length=500)


class AiCentralImageCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200)


class AiSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AiExecutionMode
    external_deidentified_enabled: bool = False
    provider_priority: list[AiSystemProviderId] = Field(min_length=1, max_length=9)
    fallback_order: list[Literal["external", "local", "internal", "rules"]] = Field(
        min_length=1,
        max_length=4,
    )
    max_attempts: int = Field(default=4, ge=1, le=8)
    total_timeout_seconds: int = Field(default=150, ge=5, le=900)
    model_roles: AiModelRoles
    central_models: AiCentralModels = Field(default_factory=AiCentralModels)
    providers: dict[AiSystemProviderId, AiProviderSettingsPatch] = Field(
        default_factory=dict,
        max_length=9,
    )

    @model_validator(mode="after")
    def validate_unique_orders(self):
        if len(self.provider_priority) != len(set(self.provider_priority)):
            raise ValueError("공급자 우선순위에 중복 항목이 있습니다.")
        if len(self.fallback_order) != len(set(self.fallback_order)):
            raise ValueError("fallback 순서에 중복 항목이 있습니다.")
        if self.fallback_order[-1] != "rules":
            raise ValueError("fallback의 마지막 경로는 규칙 기반이어야 합니다.")
        if self.mode == "fully_local" and self.external_deidentified_enabled:
            raise ValueError("완전 로컬 모드에서는 외부 전송을 활성화할 수 없습니다.")
        return self


class AiModeUpdateRequest(BaseModel):
    """Easy-mode update that cannot overwrite provider connection state."""

    model_config = ConfigDict(extra="forbid")

    mode: AiExecutionMode


class AiProviderCredentialUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(min_length=8, max_length=4096)


class AiProviderCredentialUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AiSystemProviderId
    credential_configured: Literal[True] = True
    secret_value_included: Literal[False] = False
    message: str = Field(min_length=1, max_length=200)


class AiProviderConnectRequest(BaseModel):
    """One-click external provider setup without returning the secret."""

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr | None = Field(default=None, min_length=8, max_length=4096)
    model: str | None = Field(default=None, min_length=1, max_length=200)


class AiProviderConnectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AiSystemProviderId
    connected: bool
    enabled: bool
    selected_model: str | None = Field(default=None, max_length=200)
    structured_contract_ok: bool
    latency_ms: int = Field(ge=0)
    error_code: AiFailureCode | None = None
    message: str = Field(min_length=1, max_length=300)
    deidentified_fixture_used: bool
    external_call_performed: bool
    secret_value_included: Literal[False] = False


class AiProviderTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_contract_test: bool = False


class AiProviderTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AiSystemProviderId
    connection_ok: bool
    authentication_ok: bool | None = None
    selected_model: str | None = Field(default=None, max_length=200)
    models: list[str] = Field(default_factory=list, max_length=100)
    capabilities: list[AiCapability] = Field(default_factory=list, max_length=10)
    structured_contract_ok: bool | None = None
    latency_ms: int = Field(ge=0)
    error_code: AiFailureCode | None = None
    message: str = Field(min_length=1, max_length=300)
    deidentified_fixture_used: bool = False
    external_call_performed: bool
    secret_value_included: Literal[False] = False


class AiPrivacyClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=125_000)
    contains_real_data: bool = False
    deidentified_confirmed_by_user: bool = False


class AiPrivacyClassificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: AiDataClassification
    external_allowed: bool
    reasons: list[str] = Field(min_length=1, max_length=10)
    direct_identifier_detected: bool
    care_context_detected: bool
    user_confirmation_accepted: bool


class AiRouteCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AiSystemProviderId
    model: str
    path: AiRoutePath
    processing_location: AiProcessingLocation
    external_transmission: bool
    role: Literal["primary", "fallback"]
    reason: str = Field(min_length=1, max_length=300)


class AiRoutingPreviewRequest(AiPrivacyClassificationRequest):
    capability: AiCapability = "text"
    additional_capabilities: list[AiCapability] = Field(
        default_factory=list,
        max_length=5,
    )
    model_role: Literal["text", "vision", "stt", "precision"] | None = None
    mode: AiExecutionMode | None = None


class AiRoutingPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["ai_routing_plan_v1"] = "ai_routing_plan_v1"
    selected_mode: AiExecutionMode
    data_classification: AiDataClassification
    external_transmission_allowed: bool
    candidates: list[AiRouteCandidate] = Field(min_length=1, max_length=8)
    blocked_reasons: list[str] = Field(default_factory=list, max_length=10)
    max_attempts: int = Field(ge=1, le=8)
    total_timeout_seconds: int = Field(ge=5, le=900)


class AiRuntimeRouteStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_mode: AiExecutionMode
    recommended_mode: AiExecutionMode
    current_path: AiRoutePath
    provider: AiSystemProviderId
    model: str
    processing_location: AiProcessingLocation
    external_transmission: bool
    fallback_active: bool
    fallback_state: Literal["baseline", "primary", "fallback", "blocked"]
    fallback_reason: str | None = Field(default=None, max_length=300)


class AiPrivacyPolicyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sensitive_external_default_blocked: Literal[True] = True
    uncertain_external_blocked: Literal[True] = True
    fully_local_external_blocked: Literal[True] = True
    external_requires_deidentified_confirmation: Literal[True] = True
    api_key_does_not_authorize_sensitive_transfer: Literal[True] = True


class AiSystemStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["ai_system_status_v1"] = "ai_system_status_v1"
    generated_at: datetime
    hardware: AiHardwareStatus
    recommendation: AiModeRecommendation
    runtime: AiRuntimeRouteStatus
    providers: list[AiProviderPublicStatus] = Field(min_length=1, max_length=9)
    model_roles: AiModelRoles
    model_role_options: AiRoleModelOptions = Field(default_factory=AiRoleModelOptions)
    provider_priority: list[AiSystemProviderId]
    fallback_order: list[Literal["external", "local", "internal", "rules"]]
    max_attempts: int
    total_timeout_seconds: int
    external_deidentified_enabled: bool
    privacy_policy: AiPrivacyPolicyStatus = Field(default_factory=AiPrivacyPolicyStatus)
    settings_saved: bool
    settings_write_allowed: bool = True
    secret_values_included: Literal[False] = False
    endpoint_values_included: Literal[False] = False


class AiSettingsSaveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    saved: Literal[True] = True
    mode: AiExecutionMode
    message: str = Field(min_length=1, max_length=200)
    secret_values_included: Literal[False] = False
    endpoint_values_included: Literal[False] = False


class AiModelRolesSaveResponse(AiSettingsSaveResponse):
    model_roles: AiModelRoles
