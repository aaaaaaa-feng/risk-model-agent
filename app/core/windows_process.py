from __future__ import annotations

import sys
from types import ModuleType
from typing import Any, Callable


# Win32 process creation flags.  Keep the values local instead of importing
# ``subprocess`` constants: non-Windows Python does not expose them, while the
# pure flag function is intentionally testable on every CI platform.
CREATE_NO_WINDOW = 0x0800_0000
CREATE_NEW_CONSOLE = 0x0000_0010
DETACHED_PROCESS = 0x0000_0008

_POLICY_MARKER = "_risk_agent_no_console_policy"


def no_console_creation_flags(flags: int) -> int:
    """Return Win32 flags that cannot allocate a visible console.

    ``DETACHED_PROCESS`` already starts a console program without inheriting or
    allocating a console and must not be combined with ``CREATE_NO_WINDOW``.
    Otherwise the application-level policy wins over a library's request for a
    new console, while preserving unrelated flags such as process groups.
    """

    normalized = int(flags) & ~CREATE_NEW_CONSOLE
    if normalized & DETACHED_PROCESS:
        return normalized & ~CREATE_NO_WINDOW
    return normalized | CREATE_NO_WINDOW


def install_frozen_windows_no_console_policy(
    *,
    platform_name: str | None = None,
    frozen: bool | None = None,
    winapi_module: ModuleType | Any | None = None,
) -> bool:
    """Apply one process-wide no-console policy at the Win32 launch boundary.

    Third-party libraries do not consistently forward ``subprocess`` keyword
    arguments.  CPython's Windows ``multiprocessing`` implementation also calls
    ``_winapi.CreateProcess`` directly with creation flags set to zero.  Patching
    that single low-level module boundary covers the bundled Notebook kernel,
    its helper commands and modeling workers without coupling business modules
    to private launcher implementations.

    The policy is deliberately limited to a frozen Windows application.  Source
    development keeps normal terminal semantics, and the operation is
    idempotent because every spawned frozen child executes this launcher again.
    """

    current_platform = platform_name or sys.platform
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if current_platform != "win32" or not is_frozen:
        return False

    module = winapi_module
    if module is None:  # pragma: no cover - imported only by Windows packages
        import _winapi as module  # type: ignore[import-not-found]

    if getattr(module, _POLICY_MARKER, False):
        return True

    original: Callable[..., Any] = module.CreateProcess

    def create_process_without_console(
        application_name: Any,
        command_line: Any,
        process_attributes: Any,
        thread_attributes: Any,
        inherit_handles: Any,
        creation_flags: int,
        environment: Any,
        current_directory: Any,
        startup_info: Any,
    ) -> Any:
        return original(
            application_name,
            command_line,
            process_attributes,
            thread_attributes,
            inherit_handles,
            no_console_creation_flags(creation_flags),
            environment,
            current_directory,
            startup_info,
        )

    module.CreateProcess = create_process_without_console
    setattr(module, _POLICY_MARKER, True)
    return True
