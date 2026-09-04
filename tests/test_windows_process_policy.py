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


def test_frozen_windows_policy_wraps_create_process_once(monkeypatch):
    captured_flags: list[int] = []
    system_calls: list[tuple[str, dict[str, object]]] = []
    audit_events: list[tuple[str, tuple[object, ...]]] = []

    monkeypatch.setattr(
        "app.core.windows_process.sys.audit",
        lambda event, *arguments: audit_events.append((event, arguments)),
    )

    def create_process(*arguments):
        captured_flags.append(arguments[5])
        return "process"

    def subprocess_call(command: str, **options):
        system_calls.append((command, options))
        return 17

    fake_winapi = SimpleNamespace(CreateProcess=create_process)
    fake_os = SimpleNamespace(system=lambda command: -1)

    assert install_frozen_windows_no_console_policy(
        platform_name="win32",
        frozen=True,
        winapi_module=fake_winapi,
        operating_system_module=fake_os,
        subprocess_call=subprocess_call,
    )
    wrapped = fake_winapi.CreateProcess
    wrapped_system = fake_os.system
    assert install_frozen_windows_no_console_policy(
        platform_name="win32",
        frozen=True,
        winapi_module=fake_winapi,
        operating_system_module=fake_os,
        subprocess_call=subprocess_call,
    )
    assert fake_winapi.CreateProcess is wrapped
    assert fake_os.system is wrapped_system

    result = fake_winapi.CreateProcess(None, "command", None, None, False, 0, None, None, None)
    system_result = fake_os.system("echo test")

    assert result == "process"
    assert captured_flags == [CREATE_NO_WINDOW]
    assert system_result == 17
    assert system_calls == [("echo test", {"shell": True, "creationflags": CREATE_NO_WINDOW})]
    assert audit_events == [("os.system", ("echo test",))]


def test_policy_does_not_change_source_or_non_windows_processes():
    fake_winapi = SimpleNamespace(CreateProcess=lambda *arguments: arguments)

    def original_system(command: str) -> str:
        return command

    fake_os = SimpleNamespace(system=original_system)

    assert not install_frozen_windows_no_console_policy(
        platform_name="darwin",
        frozen=True,
        winapi_module=fake_winapi,
        operating_system_module=fake_os,
    )
    assert not install_frozen_windows_no_console_policy(
        platform_name="win32",
        frozen=False,
        winapi_module=fake_winapi,
        operating_system_module=fake_os,
    )
    assert fake_os.system is original_system
