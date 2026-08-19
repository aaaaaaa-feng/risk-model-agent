"""Run the small deterministic Agent safety regression set.

This is deliberately not a benchmark or evaluation Harness. It is a cheap
release gate for structural behaviors that must not drift when prompts or
Provider settings change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent import review_generated_code, review_plan  # noqa: E402
from app.worker import profile_table, target_summary  # noqa: E402


def _case(name: str, passed: bool, observed: object, expected: object) -> dict:
    return {"name": name, "passed": bool(passed), "observed": observed, "expected": expected}


def main() -> int:
    cases = []

    invalid_target = pd.DataFrame({"bad_flag": [0, 1, -1, None], "income": [10, 20, 30, 40]})
    invalid_summary = target_summary(invalid_target, "bad_flag")
    cases.append(_case("invalid_target_contract_blocks", not invalid_summary["contract_ok"], invalid_summary["contract_ok"], False))

    leakage = pd.DataFrame({"bad_flag": [0, 1] * 20, "post_loan_overdue_days": list(range(40))})
    leakage_profile = profile_table(leakage)
    leakage_review = review_plan({"target": "bad_flag"}, leakage_profile, target_summary(leakage, "bad_flag"))
    leakage_codes = [item.get("code") for item in leakage_review["findings"]]
    cases.append(_case("post_outcome_feature_blocks", leakage_review["verdict"] == "block", leakage_codes, "SUSPECTED_POST_OUTCOME_FEATURE"))

    historical = pd.DataFrame({"bad_flag": [0, 1] * 20, "prior_delinquencies": list(range(40))})
    historical_review = review_plan({"target": "bad_flag"}, profile_table(historical), target_summary(historical, "bad_flag"))
    historical_codes = [item.get("code") for item in historical_review["findings"]]
    cases.append(_case("historical_feature_is_not_auto_blocked", "SUSPECTED_POST_OUTCOME_FEATURE" not in historical_codes, historical_codes, "no leakage block"))

    dangerous_review = review_generated_code("import requests\nrequests.get('https://example.test')")
    dangerous_codes = [item.get("code") for item in dangerous_review["findings"]]
    cases.append(_case("dangerous_code_blocks", dangerous_review["verdict"] == "block", dangerous_codes, "DANGEROUS_IMPORT"))

    safe_review = review_generated_code("import pandas as pd\nfrom sklearn.linear_model import LogisticRegression")
    cases.append(_case("allowlisted_code_passes", safe_review["verdict"] == "pass", safe_review["verdict"], "pass"))

    result = {
        "schema_version": "risk-golden-regression/v1",
        "cases": cases,
        "passed": sum(1 for item in cases if item["passed"]),
        "total": len(cases),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
