from __future__ import annotations

from types import SimpleNamespace

from app.core.windows_process import (
    CREATE_NEW_CONSOLE,
    CREATE_NO_WINDOW,
    DETACHED_PROCESS,
    install_frozen_windows_no_console_policy,
    no_console_creation_flags,
)


def test_no_console_flags_preserve_compatible_options_and_remove_new_console():
    create_new_process_group = 0x0000_0200

    flags = no_console_creation_flags(CREATE_NEW_CONSOLE | create_new_process_group)

    assert flags & CREATE_NO_WINDOW
    assert flags & create_new_process_group
    assert not flags & CREATE_NEW_CONSOLE


def test_detached_process_remains_detached_without_invalid_flag_combination():
    flags = no_console_creation_flags(DETACHED_PROCESS | CREATE_NEW_CONSOLE | CREATE_NO_WINDOW)

    assert flags == DETACHED_PROCESS
    assert not flags & CREATE_NO_WINDOW


def test_frozen_windows_policy_wraps_create_process_once():
    captured_flags: list[int] = []

    def create_process(*arguments):
        captured_flags.append(arguments[5])
        return "process"

    fake_winapi = SimpleNamespace(CreateProcess=create_process)

    assert install_frozen_windows_no_console_policy(
        platform_name="win32", frozen=True, winapi_module=fake_winapi
    )
    wrapped = fake_winapi.CreateProcess
    assert install_frozen_windows_no_console_policy(
        platform_name="win32", frozen=True, winapi_module=fake_winapi
    )
    assert fake_winapi.CreateProcess is wrapped

    result = fake_winapi.CreateProcess(None, "command", None, None, False, 0, None, None, None)

    assert result == "process"
    assert captured_flags == [CREATE_NO_WINDOW]


def test_policy_does_not_change_source_or_non_windows_processes():
    fake_winapi = SimpleNamespace(CreateProcess=lambda *arguments: arguments)

    assert not install_frozen_windows_no_console_policy(
        platform_name="darwin", frozen=True, winapi_module=fake_winapi
    )
    assert not install_frozen_windows_no_console_policy(
        platform_name="win32", frozen=False, winapi_module=fake_winapi
    )
