from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.paths import get_paths  # noqa: E402
from app.evaluation.defaults import default_suite  # noqa: E402
from app.evaluation.harness import EvaluationHarness  # noqa: E402
from app.evaluation.contracts import EvalSuite  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Risk Model Agent 本地评测 Harness")
    parser.add_argument("--suite", type=Path, help="Suite JSON；省略时使用内置合成 Smoke Suite")
    parser.add_argument("--trials", type=int, help="覆盖 Suite 的重复运行次数")
    parser.add_argument(
        "--async",
        dest="asynchronous",
        action="store_true",
        help="提交给已运行的本机 Web 服务后立即返回",
    )
    parser.add_argument(
        "--server", default="http://127.0.0.1:8765", help="异步提交的本机 Web 服务地址"
    )
    parser.add_argument("--suite-id", help="覆盖 Suite ID；修改已有 Suite 时使用新 ID")
    args = parser.parse_args()
    suite = (
        json.loads(args.suite.read_text(encoding="utf-8"))
        if args.suite
        else default_suite().model_dump(mode="json")
    )
    if args.trials is not None:
        suite["trials"] = args.trials
    if args.suite_id:
        suite["suite_id"] = args.suite_id
    suite = EvalSuite.model_validate(suite).model_dump(mode="json")
    if args.asynchronous:
        server = urlsplit(args.server)
        if (
            server.scheme != "http"
            or server.hostname not in {"localhost", "127.0.0.1", "::1"}
            or server.username
            or server.password
            or server.path not in {"", "/"}
            or server.query
            or server.fragment
        ):
            parser.error("--server 仅允许本机 HTTP 服务根地址")
        try:
            with httpx.Client(
                base_url=args.server.rstrip("/"), timeout=15, trust_env=False
            ) as client:
                saved = client.post("/api/v1/evaluations/suites", json=suite)
                saved.raise_for_status()
                response = client.post(
                    "/api/v1/evaluations/runs", json={"suite_id": suite["suite_id"]}
                )
                response.raise_for_status()
                result = response.json()["run"]
        except (httpx.HTTPError, ValueError, KeyError):
            print(
                "EVAL_ASYNC_SUBMIT_FAILED: 请确认本机 Web 服务已启动、Suite ID 未冲突且会话允许访问。",
                file=sys.stderr,
            )
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    harness = EvaluationHarness(get_paths())
    try:
        harness.save_suite(suite)
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
