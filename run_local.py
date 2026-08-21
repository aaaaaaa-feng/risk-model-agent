"""PyInstaller-friendly launcher for the Web service and bundled Notebook kernel."""

from __future__ import annotations

import multiprocessing
import sys


def main() -> None:
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

    from app.main import run

    run()


if __name__ == "__main__":
    main()
