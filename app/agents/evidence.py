from __future__ import annotations

from typing import Any

from app.core.security import validate_safe_evidence


def build_safe_evidence(
    profile: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    screening: dict[str, Any] | None = None,
    model_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    details = (profile or {}).get("columns_detail", [])
    aliases = {str(item.get("name")): f"f_{index + 1:04d}" for index, item in enumerate(details)}
    screening_map = {
        str(item.get("column")): item for item in (screening or {}).get("features", [])
    }
    fields = []
    for item in details:
        original = str(item.get("name"))
        selected = screening_map.get(original, {})
        fields.append(
            {
                "alias": aliases[original],
                "type": item.get("type"),
                "missing_rate": item.get("missing_rate"),
                "unique_count": item.get("unique_count"),
                "pii": bool(item.get("pii")),
                "selected": selected.get("status") == "included",
                "selection_reason": selected.get("reason"),
                "iv": selected.get("iv"),
            }
        )
    candidates = []
    for item in (model_result or {}).get("candidates", []):
        candidates.append(
            {
                "algorithm": item.get("candidate"),
                "calibration": item.get("calibration"),
                "test_metrics": item.get("test_metrics"),
                "test_monotonicity": item.get("test_monotonicity"),
                "train_test_score_psi": item.get("train_test_score_psi"),
                "fit_scope": item.get("fit_scope"),
                "selection_scope": item.get("selection_scope"),
            }
        )
    evidence = {
        "schema_version": "risk-safe-evidence/v1",
        "dataset": {
            "row_count": int((profile or {}).get("rows") or 0),
            "column_count": int((profile or {}).get("columns") or 0),
            "duplicate_count": int((profile or {}).get("duplicate_rows") or 0),
        },
        "target": {
            key: (target or {}).get(key)
            for key in (
                "valid_count",
                "positive_count",
                "negative_count",
                "bad_rate",
                "invalid_count",
                "missing_count",
            )
        },
        "fields": fields,
        "selection_thresholds": (screening or {}).get("thresholds", {}),
        "candidate_models": candidates,
        "champion": (model_result or {}).get("champion"),
        "governance": {
            "raw_data_included": False,
            "original_column_names_included": False,
            "credentials_included": False,
            "minimum_aggregate_count": 30,
        },
    }
    validate_safe_evidence(evidence)
    return evidence, aliases
