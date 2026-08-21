"""Check the cross-platform packaging contract without importing PyInstaller.

This is intentionally a source-level check. A real macOS/Windows build still
has to run on the target operating system with the optional package extra.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    required_files = [
        "run_local.py",
        "packaging/risk_model_agent.spec",
        "packaging/windows_installer.iss",
        "packaging/languages/ChineseSimplified.isl",
        "packaging/languages/ChineseSimplified.LICENSE.txt",
        "scripts/build_mac.sh",
        "scripts/build_windows.ps1",
        "scripts/compile_windows_installer.ps1",
        "scripts/smoke_windows_installer.ps1",
        "scripts/smoke_packaged_service.py",
        "scripts/start_mac.command",
        "scripts/start_windows.ps1",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/src/App.tsx",
    ]
    missing = [path for path in required_files if not (ROOT / path).is_file()]
    spec = (ROOT / "packaging/risk_model_agent.spec").read_text(encoding="utf-8") if not missing else ""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    installer = (
        (ROOT / "packaging/windows_installer.iss").read_text(encoding="utf-8")
        if not missing
        else ""
    )
    contract = {
        "schema_version": "risk-packaging-contract/v1",
        "required_files": len(required_files),
        "missing_files": missing,
        "spec_has_onedir_collect": "COLLECT(" in spec,
        "spec_has_local_assets": "frontend_dist" in spec and "frontend" in spec,
        "spec_has_scoring_helper": (
            '(str(ROOT / "app" / "workers" / "scoring.py"), "app/workers")' in spec
        ),
        "spec_has_notebook_libraries": all(
            name in spec for name in ('"polars"', '"duckdb"', '"debugpy"')
        ),
        "launcher_dispatches_kernel": "IPKernelApp.launch_instance()" in (
            ROOT / "run_local.py"
        ).read_text(encoding="utf-8"),
        "spec_has_launcher": "run_local.py" in spec,
        "spec_uses_repository_root": "ROOT = Path(SPECPATH).resolve().parent\n" in spec,
        "pyinstaller_optional_dependency": "pyinstaller" in pyproject.lower(),
        "windows_installer_is_per_user": (
            "PrivilegesRequired=lowest" in installer
            and "DefaultDirName={localappdata}\\Programs\\RiskModelAgent" in installer
        ),
        "windows_installer_is_x64": "ArchitecturesAllowed=x64compatible" in installer,
        "windows_installer_keeps_user_data": not any(
            line.strip().lower() == "[uninstalldelete]" for line in installer.splitlines()
        ),
        "windows_installer_has_uninstall_entry": "{uninstallexe}" in installer,
        "windows_installer_has_localized_messages": (
            "languages\\ChineseSimplified.isl" in installer
        ),
    }
    contract["valid"] = not missing and all(
        bool(contract[key])
        for key in (
            "spec_has_onedir_collect",
            "spec_has_local_assets",
            "spec_has_scoring_helper",
            "spec_has_notebook_libraries",
            "launcher_dispatches_kernel",
            "spec_has_launcher",
            "spec_uses_repository_root",
            "pyinstaller_optional_dependency",
            "windows_installer_is_per_user",
            "windows_installer_is_x64",
            "windows_installer_keeps_user_data",
            "windows_installer_has_uninstall_entry",
            "windows_installer_has_localized_messages",
        )
    )
    print(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True))
    if not contract["valid"]:
        print("packaging contract check failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
