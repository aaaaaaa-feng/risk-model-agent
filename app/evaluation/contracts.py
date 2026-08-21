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
    category: Literal["core", "edge", "safety", "recovery", "badcase"] = "core"
    severity: Literal["low", "medium", "high", "critical"] = "medium"
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


class EvalGate(BaseModel):
    """Deterministic release thresholds for a Harness run.

    These are deliberately conservative defaults, not claims about production
    performance.  A suite may override them and the full gate configuration is
    persisted with the run so that later comparisons remain reproducible.
    """

    min_expectation_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    max_error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    max_security_event_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    max_average_tokens: int | None = Field(default=None, ge=0)
    require_trace_for_each_case: bool = True


class EvalSuite(BaseModel):
    """Versioned, local-only evaluation suite definition."""

    suite_id: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=200)
    version: str = "risk-agent-eval-suite/v1"
    description: str = ""
    cases: list[EvalCase] = Field(min_length=1, max_length=100)
    trials: int = Field(default=1, ge=1, le=10)
    gate: EvalGate = Field(default_factory=EvalGate)
    holdout: bool = False

    @model_validator(mode="after")
    def validate_unique_cases(self) -> Self:
        identifiers = [item.case_id for item in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("EVAL_SUITE_DUPLICATE_CASE_ID")
        if self.holdout and any(item.category == "badcase" for item in self.cases):
            raise ValueError("EVAL_HOLDOUT_BADCASE_MIX_FORBIDDEN")
        return self


class EvalRun(BaseModel):
    schema_version: str = "risk-agent-eval-run/v1"
    run_id: str
    suite_id: str
    suite_version: str
    status: Literal["queued", "running", "completed", "failed"]
    started_at: str | None = None
    finished_at: str | None = None
    result_paths: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    gate: dict[str, Any] = Field(default_factory=dict)
    baseline_run_id: str | None = None
    comparison: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
