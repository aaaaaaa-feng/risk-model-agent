from __future__ import annotations

from .contracts import EvalCase, EvalGate, EvalSuite


def default_suite() -> EvalSuite:
    """Small deterministic suite for local smoke/PR validation.

    It uses the synthetic fixture only.  It is a framework check, not a claim
    about real portfolio performance or a production Provider.
    """

    return EvalSuite(
        suite_id="risk-agent-smoke-v1",
        name="Risk Model Agent 本地 Smoke Suite",
        version="risk-agent-eval-suite/v1",
        description="覆盖核心成功、Reviewer 阻断和 Worker 故障恢复的合成用例。",
        cases=[
            EvalCase(
                case_id="core_smoke_001",
                category="core",
                mode="semi_trusted",
                provider_profile="deterministic",
                expected_terminal_state="succeeded",
                rows=500,
            ),
            EvalCase(
                case_id="safety_reviewer_block_001",
                category="safety",
                severity="high",
                mode="fully_trusted",
                provider_profile="fake_provider",
                faults=["reviewer_block"],
                expected_terminal_state="blocked",
                rows=500,
            ),
            EvalCase(
                case_id="recovery_worker_error_001",
                category="recovery",
                provider_profile="fake_provider",
                faults=["worker_error:diagnose_data"],
                expected_terminal_state="failed",
                rows=500,
            ),
        ],
        trials=1,
        gate=EvalGate(max_security_event_rate=1.0),
    )
