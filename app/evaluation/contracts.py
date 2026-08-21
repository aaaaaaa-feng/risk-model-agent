from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator


class EvalDecision(BaseModel):
    stage: str | None = None
    kind: str | None = None
    approved: bool = True
    edits: dict[str, Any] = Field(default_factory=dict)


class EvalCase(BaseModel):
    case_id: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    suite_version: str = "risk-agent-eval/v1"
    evaluator_version: str = "risk-agent-target-adapter/v1"
    goal: str = "为给定目标变量完成受控建模流程"
    fixture: Literal["synthetic_time_oot_v1"] = "synthetic_time_oot_v1"
    mode: Literal["semi_trusted", "fully_trusted"] = "semi_trusted"
    target: Literal["FPD0", "FPD7", "MOB30"] = "FPD0"
    user_decisions: list[EvalDecision] = Field(default_factory=list)
    provider_profile: Literal["deterministic", "fake_provider", "configured_provider"] = (
        "deterministic"
    )
    faults: list[str] = Field(default_factory=list)
    expected_terminal_state: Literal["succeeded", "failed", "blocked"] = "succeeded"
    rows: int = Field(default=600, ge=500, le=10_000)
    timeout_seconds: int = Field(default=300, ge=10, le=3600)
    cleanup_workspace: bool = True

    @field_validator("faults")
    @classmethod
    def validate_faults(cls, values: list[str]) -> list[str]:
        supported = {
            "provider_timeout",
            "provider_auth_failed",
            "provider_forbidden",
            "provider_rate_limited",
            "provider_empty_response",
            "provider_invalid_json",
            "reviewer_block",
            "reviewer_revise",
        }
        for value in values:
            if value in supported or value.startswith(("worker_error:", "worker_timeout:")):
                continue
            raise ValueError(f"EVAL_FAULT_UNSUPPORTED: {value}")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_fault_profile(self) -> Self:
        provider_faults = [
            value
            for value in self.faults
            if value.startswith("provider_") or value.startswith("reviewer_")
        ]
        if provider_faults and self.provider_profile != "fake_provider":
            raise ValueError("EVAL_PROVIDER_FAULT_REQUIRES_FAKE_PROVIDER")
        return self


class EvalResult(BaseModel):
    schema_version: str = "risk-agent-eval-result/v1"
    case_id: str
    trial_id: str
    run_id: str | None = None
    terminal_state: str
    expected_terminal_state: str
    expectation_met: bool
    final_response: str
    trace_bundle_path: str | None = None
    artifact_manifest_path: str | None = None
    security_events: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
