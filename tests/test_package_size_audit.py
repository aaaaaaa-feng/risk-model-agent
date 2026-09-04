import importlib
import json
from pathlib import Path

import pytest

import scripts.audit_package_size as package_size_audit
from scripts.audit_package_size import (
    InstallerPolicy,
    PackageAuditError,
    create_report,
    resolve_installer,
)


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


def _write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_package_audit_reports_components_and_passes_both_size_gates(tmp_path: Path):
    bundle = tmp_path / "risk-model-agent"
    _write(bundle / "risk-model-agent.exe", 300)
    _write(bundle / "_internal" / "duckdb" / "__init__.pyc", 400)
    installer = _write(tmp_path / "installer" / "RiskModelAgent-1.1.0-windows-x64-setup.exe", 600)

    report = create_report(
        bundle,
        installer=installer,
        embedded_modules=(),
        policy=InstallerPolicy(
            baseline_kib=2,
            maximum_mib=1,
            minimum_reduction_percent=25,
        ),
    )

    assert report["valid"] is True
    assert report["bundle"]["bytes"] == 700
    assert report["installer"]["within_maximum"] is True
    assert report["installer"]["meets_reduction"] is True


@pytest.mark.parametrize(
    "relative",
    [
        "_internal/_polars_runtime_32/runtime.pyd",
        "_internal/matplotlib/backends.pyc",
        "_internal/xgboost/testing/data.pyc",
        "_internal/debugpy/server.pyc",
        "_internal/uvloop/loop.pyd",
        "_internal/IPython/core/interactiveshell.pyc",
        "_internal/ipykernel/kernelapp.pyc",
        "_internal/jupyter_client/manager.pyc",
        "_internal/nbformat/__init__.pyc",
        "_internal/zmq/backend/cython/_zmq.pyd",
    ],
)
def test_package_audit_rejects_forbidden_or_test_components(tmp_path: Path, relative: str):
    bundle = tmp_path / "risk-model-agent"
    _write(bundle / relative, 10)

    report = create_report(bundle, embedded_modules=())

    assert report["valid"] is False
    assert report["forbidden_components"]["paths"] == [relative]


def test_package_audit_requires_both_absolute_and_relative_size_limits(tmp_path: Path):
    bundle = tmp_path / "risk-model-agent"
    _write(bundle / "risk-model-agent.exe", 10)
    installer = _write(tmp_path / "RiskModelAgent-1.1.0-windows-x64-setup.exe", 800)

    report = create_report(
        bundle,
        installer=installer,
        embedded_modules=(),
        policy=InstallerPolicy(
            baseline_kib=1,
            maximum_mib=1,
            minimum_reduction_percent=25,
        ),
    )

    assert report["installer"]["within_maximum"] is True
    assert report["installer"]["meets_reduction"] is False
    assert report["valid"] is False


def test_installer_directory_must_contain_exactly_one_candidate(tmp_path: Path):
    installer_dir = tmp_path / "installer"
    _write(installer_dir / "RiskModelAgent-1.0.2-windows-x64-setup.exe", 1)
    _write(installer_dir / "RiskModelAgent-1.1.0-windows-x64-setup.exe", 1)

    with pytest.raises(PackageAuditError, match="必须唯一"):
        resolve_installer(installer_dir)


def test_package_audit_rejects_forbidden_modules_inside_executable(tmp_path: Path):
    bundle = tmp_path / "risk-model-agent"
    _write(bundle / "risk-model-agent.exe", 10)

    report = create_report(
        bundle,
        embedded_modules=("app.main", "IPython.core.interactiveshell", "nbformat.validator"),
    )

    assert report["valid"] is False
    assert report["forbidden_components"]["paths"] == []
    assert report["forbidden_components"]["embedded_modules"] == [
        "IPython.core.interactiveshell",
        "nbformat.validator",
    ]


@pytest.mark.parametrize(
    "module_name",
    (
        "app.notebooks",
        "app.notebooks.runtime",
        "app.api.notebooks",
        "app.agents.codegen",
    ),
)
def test_package_audit_rejects_retired_notebook_application_modules(
    tmp_path: Path, module_name: str
):
    bundle = tmp_path / "risk-model-agent"
    _write(bundle / "risk-model-agent.exe", 10)

    report = create_report(bundle, embedded_modules=("app.main", module_name))

    assert report["valid"] is False
    assert report["forbidden_components"]["embedded_modules"] == [module_name]


def test_packaging_source_guard_is_recursive_and_covers_codegen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scripts_directory = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_directory))
    verify_packaging = importlib.import_module("verify_packaging")

    assert verify_packaging.notebook_sources_are_removed(tmp_path) is True

    nested_runtime = tmp_path / "app" / "notebooks" / "nested" / "runtime.py"
    nested_runtime.parent.mkdir(parents=True)
    nested_runtime.write_text("", encoding="utf-8")
    assert verify_packaging.notebook_sources_are_removed(tmp_path) is False

    nested_runtime.unlink()
    codegen = tmp_path / "app" / "agents" / "codegen.py"
    codegen.parent.mkdir(parents=True)
    codegen.write_text("", encoding="utf-8")
    assert verify_packaging.notebook_sources_are_removed(tmp_path) is False


def test_embedded_module_audit_does_not_misclassify_vendored_optional_names(tmp_path: Path):
    bundle = tmp_path / "risk-model-agent"
    _write(bundle / "risk-model-agent.exe", 10)

    report = create_report(
        bundle,
        embedded_modules=(
            "dotenv.ipython",
            "numpy.testing",
            "pygments.lexers.graphviz",
            "scipy._external.array_api_compat.dask",
        ),
    )

    assert report["valid"] is True
    assert report["forbidden_components"]["embedded_modules"] == []


def test_package_audit_keeps_utf8_report_and_supports_cp1252_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    bundle = tmp_path / "中文目录" / "risk-model-agent"
    _write(bundle / "risk-model-agent.exe", 10)
    output = tmp_path / "中文报告.json"
    console = _Cp1252Console()
    monkeypatch.setattr(package_size_audit, "embedded_python_modules", lambda _bundle: ())
    monkeypatch.setattr(package_size_audit.sys, "stdout", console)

    exit_code = package_size_audit.main(
        ["--bundle", str(bundle), "--output", str(output), "--enforce"]
    )

    assert exit_code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert "中文目录" in report["bundle"]["path"]
    assert report["valid"] is True
    assert "schema_version" in console.text
    assert "\\u" in console.text
    console.text.encode("cp1252")


def test_package_audit_help_supports_cp1252_stdout(
    monkeypatch: pytest.MonkeyPatch,
):
    console = _Cp1252Console()
    monkeypatch.setattr(package_size_audit.sys, "stdout", console)

    with pytest.raises(SystemExit) as raised:
        package_size_audit.main(["--help"])

    assert raised.value.code == 0
    assert "--bundle" in console.text
    assert "\\u" in console.text
    console.text.encode("cp1252")


def test_package_audit_argument_error_supports_cp1252_stderr(
    monkeypatch: pytest.MonkeyPatch,
):
    console = _Cp1252Console()
    monkeypatch.setattr(package_size_audit.sys, "stderr", console)

    with pytest.raises(SystemExit) as raised:
        package_size_audit.main(["--baseline-kib", "not-a-number"])

    assert raised.value.code == 2
    assert "--baseline-kib" in console.text
    console.text.encode("cp1252")
