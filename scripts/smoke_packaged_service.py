"""Exercise the installed localhost bundle beyond a page-load smoke test.

The script intentionally uses only Python's standard library so the package jobs
can run it from the GitHub-hosted Python after starting the frozen application.
All data is fixed-seed synthetic data and all HTTP traffic stays on localhost.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    body = None
    headers: dict[str, str] = {}
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
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="kind"\r\n\r\n'
        "score_input\r\n",
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="packaged-score-input.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n",
    ]
    body = parts[0].encode() + parts[1].encode() + source.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    request = Request(
        f"{base_url}/api/v1/projects/{project_id}/data-assets",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
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
    args = parser.parse_args()
    base_url = args.url.rstrip("/")

    health = request_json(base_url, "GET", "/api/v1/health", timeout=30)
    assert health["status"] == "ok"
    assert health["runtime"] == "local"
    assert health["raw_data_cloud_upload"] is False

    project = request_json(
        base_url,
        "POST",
        "/api/v1/projects",
        {"name": "Packaged Notebook Smoke", "mode": "fully_trusted"},
    )["project"]
    notebook = request_json(
        base_url,
        "POST",
        "/api/v1/notebooks",
        {"project_id": project["id"], "name": "Bundled dependencies"},
    )["notebook"]
    execution = request_json(
        base_url,
        "POST",
        f"/api/v1/notebooks/{notebook['id']}/execute-cell",
        {"cell_index": 1, "timeout_seconds": 150},
    )["execution"]
    assert execution["status"] == "succeeded", execution

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
    assert state["report_review"]["status"] == "pass"

    artifact_payload = request_json(
        base_url, "GET", f"/api/v1/runs/{run['id']}/artifacts", timeout=30
    )
    artifacts = {item["kind"]: item for item in artifact_payload["artifacts"]}
    required = {
        "report_json",
        "report_excel",
        "report_html",
        "reproducible_notebook",
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

    print(
        json.dumps(
            {
                "status": "passed",
                "notebook": execution["status"],
                "run_id": run["id"],
                "champion": result["champion"],
                "quality_verdict": state["report"]["executive_summary"]["quality_verdict"],
                "artifact_kinds": sorted(required),
                "score_rows": job["rows"],
                "score_column": job["metadata"]["score_column"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
