"""Exercise the installed localhost bundle beyond a page-load smoke test.

The script intentionally uses only Python's standard library so the package jobs
can run it from the GitHub-hosted Python after starting the frozen application.
All data is fixed-seed synthetic data and all HTTP traffic stays on localhost.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DESKTOP_COOKIE_ENV = "RISK_AGENT_SMOKE_DESKTOP_COOKIE"
DESKTOP_COOKIE_NAME = "risk_agent_desktop_session"
DESKTOP_COOKIE_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _desktop_session_headers() -> dict[str, str]:
    value = os.environ.get(DESKTOP_COOKIE_ENV)
    if value is None:
        return {}
    if not DESKTOP_COOKIE_PATTERN.fullmatch(value):
        raise RuntimeError("桌面冒烟会话格式无效，已阻止请求。")
    return {"Cookie": f"{DESKTOP_COOKIE_NAME}={value}"}


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    body = None
    headers = _desktop_session_headers()
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{base_url}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 -- fixed localhost URL
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {detail}") from exc


def upload_csv(base_url: str, project_id: str, source: Path) -> dict[str, Any]:
    boundary = f"----risk-model-agent-{uuid.uuid4().hex}"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="kind"\r\n\r\nscore_input\r\n',
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="packaged-score-input.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n",
    ]
    body = parts[0].encode() + parts[1].encode() + source.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    request = Request(
        f"{base_url}/api/v1/projects/{project_id}/data-assets",
        data=body,
        headers={
            **_desktop_session_headers(),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:  # noqa: S310 -- fixed localhost URL
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"score input upload failed: HTTP {exc.code}: {detail}") from exc


def wait_for_run(base_url: str, run_id: str, timeout: int = 240) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = request_json(base_url, "GET", f"/api/v1/runs/{run_id}", timeout=30)["run"]
        if run["status"] in {"succeeded", "failed", "blocked"}:
            return run
        time.sleep(1)
    raise TimeoutError(f"packaged run {run_id} did not finish in {timeout} seconds")


def assert_sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise AssertionError(f"{label} does not contain a SHA-256 checksum")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--evidence-output",
        default="",
        help="可选：把升级前的项目与非秘密 Provider 配置证据写入 JSON。",
    )
    args = parser.parse_args()
    base_url = args.url.rstrip("/")

    health = request_json(base_url, "GET", "/api/v1/health", timeout=30)
    assert health["status"] == "ok"
    assert health["runtime"] == "local"
    if health.get("desktop") is True:
        if not _desktop_session_headers():
            raise AssertionError("桌面后端冒烟未携带 WebView 已建立的会话。")
    else:
        assert health["raw_data_cloud_upload"] is False

    project = request_json(
        base_url,
        "POST",
        "/api/v1/projects",
        {"name": "Packaged API Smoke", "mode": "fully_trusted"},
    )["project"]

    demo = request_json(
        base_url,
        "POST",
        "/api/v1/projects/demo",
        {
            "name": "Packaged Golden Flow",
            "mode": "fully_trusted",
            "rows": 600,
            "seed": 20260821,
        },
        timeout=180,
    )
    created = request_json(
        base_url,
        "POST",
        "/api/v1/runs",
        {
            "project_id": demo["project"]["id"],
            "target_task_id": demo["target_tasks"][0]["id"],
            "mode": "fully_trusted",
        },
    )["run"]
    run = wait_for_run(base_url, created["id"])
    if run["status"] != "succeeded":
        raise AssertionError({"status": run["status"], "error": run.get("error")})

    state = run["state"]
    result = state["model_result"]
    assert result["champion"]
    assert result["oot_used_for_selection"] is False
    assert state["report"]["executive_summary"]["quality_verdict"] in {"pass", "conditional"}
    assert state["report_review"]["status"] in {"pass", "fallback_pass"}

    artifact_payload = request_json(
        base_url, "GET", f"/api/v1/runs/{run['id']}/artifacts", timeout=30
    )
    artifacts = {item["kind"]: item for item in artifact_payload["artifacts"]}
    required = {
        "report_json",
        "report_excel",
        "report_html",
        "model_package",
    }
    assert required.issubset(artifacts), sorted(artifacts)
    for kind in required:
        assert_sha256(artifacts[kind]["checksum"], kind)

    score_asset = upload_csv(
        base_url,
        demo["project"]["id"],
        Path(demo["dataset_version"]["stored_path"]),
    )["asset"]
    score = request_json(
        base_url,
        "POST",
        "/api/v1/score-jobs",
        {
            "model_version_id": state["model_version_id"],
            "input_asset_id": score_asset["id"],
        },
        timeout=180,
    )
    job = score["score_job"]
    assert job["status"] == "succeeded"
    assert job["rows"] == 600
    assert job["metadata"]["score_column"].startswith("FPD0_")
    assert_sha256(job["metadata"]["output_sha256"], "score output")

    evidence: dict[str, Any] | None = None
    if args.evidence_output:
        evidence_token = uuid.uuid4().hex
        profile_id = f"migration-{evidence_token}"
        model_marker = f"migration-model-{evidence_token}"
        reviewer_marker = f"migration-reviewer-{evidence_token}"
        provider_response = request_json(
            base_url,
            "PUT",
            "/api/v1/providers/settings",
            {
                "profile_id": profile_id,
                "provider": "deepseek",
                "api_format": "openai",
                "base_url": "https://api.deepseek.com/v1",
                "model": model_marker,
                "reviewer_model": reviewer_marker,
                "llm_enabled": False,
            },
            timeout=30,
        )
        settings = provider_response["settings"]
        assert settings["active_profile_id"] == profile_id
        assert settings["api_key_configured"] is False
        evidence = {
            "schema_version": "risk-windows-migration-evidence/v1",
            "projects": [
                {"id": project["id"], "name": project["name"]},
                {
                    "id": demo["project"]["id"],
                    "name": demo["project"]["name"],
                },
            ],
            "provider": {
                "active_profile_id": profile_id,
                "provider": settings["provider"],
                "api_format": settings["api_format"],
                "base_url": settings["base_url"],
                "model": model_marker,
                "reviewer_model": reviewer_marker,
                "llm_enabled": False,
                "api_key_configured": False,
            },
        }
        evidence_path = Path(args.evidence_output).expanduser().resolve()
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = evidence_path.with_name(f".{evidence_path.name}.tmp")
        temporary_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(evidence_path)

    print(
        json.dumps(
            {
                "status": "passed",
                "run_id": run["id"],
                "champion": result["champion"],
                "quality_verdict": state["report"]["executive_summary"]["quality_verdict"],
                "artifact_kinds": sorted(required),
                "score_rows": job["rows"],
                "score_column": job["metadata"]["score_column"],
                "migration_evidence": bool(evidence),
            },
            # 结果可能包含中文质检结论，ASCII 转义可兼容 Windows 旧代码页。
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
