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
        "packaging/README.md",
        "scripts/build_mac.sh",
        "scripts/build_windows.ps1",
        "scripts/start_mac.command",
        "scripts/start_windows.ps1",
        "app/templates/index.html",
        "app/static/app.js",
        "app/static/styles.css",
    ]
    missing = [path for path in required_files if not (ROOT / path).is_file()]
    spec = (ROOT / "packaging/risk_model_agent.spec").read_text(encoding="utf-8") if not missing else ""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    contract = {
        "schema_version": "risk-packaging-contract/v1",
        "required_files": len(required_files),
        "missing_files": missing,
        "spec_has_onedir_collect": "COLLECT(" in spec,
        "spec_has_local_assets": "app/templates" in spec and "app/static" in spec,
        "spec_has_launcher": "run_local.py" in spec,
        "spec_uses_repository_root": "ROOT = Path(SPECPATH).resolve().parent\n" in spec,
        "pyinstaller_optional_dependency": "pyinstaller" in pyproject.lower(),
    }
    contract["valid"] = not missing and all(
        bool(contract[key])
        for key in (
            "spec_has_onedir_collect",
            "spec_has_local_assets",
            "spec_has_launcher",
            "spec_uses_repository_root",
            "pyinstaller_optional_dependency",
        )
    )
    print(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True))
    if not contract["valid"]:
        print("packaging contract check failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
