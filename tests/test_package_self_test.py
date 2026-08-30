from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import app.packaging.self_test as package_self_test
import pytest
from app.packaging.self_test import REQUIRED_MODELS, run_package_self_test


ROOT = Path(__file__).resolve().parent.parent


class _Cp1252Console:
    """模拟 Windows 旧代码页，写入不可编码字符时立即失败。"""

    encoding = "cp1252"

    def __init__(self) -> None:
        self.fragments: list[str] = []

    def write(self, value: str) -> int:
        value.encode(self.encoding)
        self.fragments.append(value)
        return len(value)

    def flush(self) -> None:
        return None

    @property
    def text(self) -> str:
        return "".join(self.fragments)


def test_package_self_test_fits_every_required_model_and_notebook_contract():
    report = run_package_self_test()

    assert report["status"] == "passed", report
    assert {item["id"] for item in report["models"]} >= REQUIRED_MODELS
    assert all(item["status"] == "passed" for item in report["models"])
    assert report["notebook"]["status"] == "passed"
    assert report["notebook"]["dependencies"] == {
        "pandas": True,
        "numpy": True,
        "duckdb": True,
        "nbformat": True,
        "jupyter_client": True,
        "ipykernel": True,
    }
    assert report["serialization"]["status"] == "passed"
    assert report["serialization"]["format"] == "skops"
    assert report["network_used"] is False
    assert report["user_workspace_written"] is False
    assert report["random_seed"] == 42


def test_package_self_test_fails_closed_when_a_required_model_cannot_load(monkeypatch):
    original = package_self_test.MODEL_REGISTRY.build

    def fail_xgboost(identifier, frame, features, positives, negatives):
        if identifier == "xgboost":
            raise ImportError("xgboost native library missing")
        return original(identifier, frame, features, positives, negatives)

    monkeypatch.setattr(package_self_test.MODEL_REGISTRY, "build", fail_xgboost)

    report = run_package_self_test()

    xgboost = next(item for item in report["models"] if item["id"] == "xgboost")
    assert report["status"] == "failed"
    assert xgboost["status"] == "failed"
    assert "ImportError" in xgboost["error"]


def test_launcher_dispatches_internal_package_self_test_without_starting_web_service():
    completed = subprocess.run(
        [sys.executable, "run_local.py", "--internal-package-self-test"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "passed"
    assert {item["id"] for item in report["models"]} >= REQUIRED_MODELS


def test_package_self_test_cli_supports_cp1252_stdout(monkeypatch: pytest.MonkeyPatch):
    console = _Cp1252Console()
    report = {
        "schema_version": "risk-package-self-test/v1",
        "status": "passed",
        "message": "冻结包能力自检通过",
    }
    monkeypatch.setattr(package_self_test, "run_package_self_test", lambda: report)
    monkeypatch.setattr(package_self_test.sys, "stdout", console)

    assert package_self_test.main() == 0
    assert json.loads(console.text) == report
    assert "\\u" in console.text
    console.text.encode("cp1252")
