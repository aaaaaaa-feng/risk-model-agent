from pathlib import Path

import pytest

from scripts.audit_package_size import (
    InstallerPolicy,
    PackageAuditError,
    create_report,
    resolve_installer,
)


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
    ],
)
def test_package_audit_rejects_forbidden_or_test_components(tmp_path: Path, relative: str):
    bundle = tmp_path / "risk-model-agent"
    _write(bundle / relative, 10)

    report = create_report(bundle)

    assert report["valid"] is False
    assert report["forbidden_components"]["paths"] == [relative]


def test_package_audit_requires_both_absolute_and_relative_size_limits(tmp_path: Path):
    bundle = tmp_path / "risk-model-agent"
    _write(bundle / "risk-model-agent.exe", 10)
    installer = _write(tmp_path / "RiskModelAgent-1.1.0-windows-x64-setup.exe", 800)

    report = create_report(
        bundle,
        installer=installer,
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
