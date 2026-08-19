from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_packaging_contract_is_complete() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_packaging.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
