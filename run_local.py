"""PyInstaller-friendly launcher for the Web service and bundled Notebook kernel."""

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
    # Install the Windows policy before freeze_support or any application/
    # Jupyter imports.  This is the one boundary shared by Notebook kernels,
    # model workers and third-party subprocess helpers in the frozen package.
    from app.core.windows_process import install_frozen_windows_no_console_policy

    install_frozen_windows_no_console_policy()
    # PyInstaller multiprocessing children must be dispatched before importing
    # the FastAPI application, which creates databases, thread pools and the
    # LangGraph runtime at module import time.
    multiprocessing.freeze_support()
    # Jupyter's default Python kernelspec launches ``sys.executable -m
    # ipykernel_launcher``. In a frozen application ``sys.executable`` is this
    # launcher, so dispatch to the bundled kernel instead of recursively
    # starting another Web service.
    if sys.argv[1:3] == ["-m", "ipykernel_launcher"]:
        sys.argv = [sys.argv[0], *sys.argv[3:]]
        from ipykernel.kernelapp import IPKernelApp

        IPKernelApp.launch_instance()
        return
    if sys.argv[1:] == ["--internal-package-self-test"]:
        from app.packaging.self_test import main as self_test_main

        raise SystemExit(self_test_main())

    from app.main import run

    run()


if __name__ == "__main__":
    main()
