from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.paths import get_paths  # noqa: E402
from app.evaluation.defaults import default_suite  # noqa: E402
from app.evaluation.harness import EvaluationHarness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Risk Model Agent 本地评测 Harness")
    parser.add_argument("--suite", type=Path, help="Suite JSON；省略时使用内置合成 Smoke Suite")
    parser.add_argument("--trials", type=int, help="覆盖 Suite 的重复运行次数")
    parser.add_argument("--async", dest="asynchronous", action="store_true", help="提交后不等待")
    args = parser.parse_args()

    harness = EvaluationHarness(get_paths())
    try:
        if args.suite:
            suite_payload = json.loads(args.suite.read_text(encoding="utf-8"))
            suite = harness.save_suite(suite_payload)
        else:
            suite = default_suite().model_dump(mode="json")
            if args.trials is not None:
                suite["trials"] = args.trials
            harness.save_suite(suite)
        if args.asynchronous:
            result = harness.start_run(suite["suite_id"])
        else:
            result = harness.run_now(suite["suite_id"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return (
            0
            if result.get("status") == "completed" and (result.get("gate") or {}).get("passed")
            else 1
        )
    finally:
        harness.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
