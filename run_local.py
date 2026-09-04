"""PyInstaller-friendly launcher for the local Web service and model workers."""

from __future__ import annotations

import multiprocessing
import os
import sys


def _ensure_frozen_stdio() -> None:
    """为 Windows windowed 冻结进程提供无终端的安全输出目标。"""

    if sys.stdout is not None and sys.stderr is not None:
        return
    target = os.getenv("RISK_AGENT_BACKEND_LOG_PATH") or os.devnull
    if sys.stdout is None:
        sys.stdout = open(target, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(target, "a", encoding="utf-8", buffering=1)  # noqa: SIM115


def main() -> None:
    _ensure_frozen_stdio()
    # Install the Windows policy before freeze_support or any application
    # imports. This boundary is shared by model workers and third-party
    # subprocess helpers in the frozen package.
    from app.core.windows_process import install_frozen_windows_no_console_policy

    install_frozen_windows_no_console_policy()
    # PyInstaller multiprocessing children must be dispatched before importing
    # the FastAPI application, which creates databases, thread pools and the
    # LangGraph runtime at module import time.
    multiprocessing.freeze_support()
    if sys.argv[1:] == ["--internal-package-self-test"]:
        from app.packaging.self_test import main as self_test_main

        raise SystemExit(self_test_main())

    from app.main import run

    run()


if __name__ == "__main__":
    main()
