"""静态验证 Inno Setup 到 Tauri NSIS 的安全迁移边界。"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = ROOT / "desktop" / "src-tauri" / "windows" / "installer-hooks.nsh"
CONFIG_PATH = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
LEGACY_KEY = (
    "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
    "{4CE3329A-CF6F-49E0-86C7-BE5C38DB1474}_is1"
)


def test_tauri_nsis_registers_the_legacy_inno_migration_hook() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    nsis = config["bundle"]["windows"]["nsis"]

    assert nsis["installMode"] == "currentUser"
    assert nsis["installerHooks"] == "windows/installer-hooks.nsh"
    assert HOOK_PATH.is_file()


def test_legacy_migration_is_fixed_hkcu_fail_closed_and_silent() -> None:
    hook = HOOK_PATH.read_text(encoding="utf-8")

    assert LEGACY_KEY in hook
    assert "RegOpenKeyExW" in hook
    assert "KEY_QUERY_VALUE|KEY_WOW64_64KEY" in hook
    assert "0x0101" in hook
    assert '${If} $R6 == "1"' in hook
    assert '"DisplayName"' in hook
    assert '"Publisher"' in hook
    assert '"DisplayVersion"' in hook
    assert '"InstallLocation"' in hook
    assert '"UninstallString"' in hook
    assert '"QuietUninstallString"' in hook
    assert 'IfFileExists "$R5\\risk-model-agent.exe"' in hook
    assert 'IfFileExists "$R5\\unins000.exe"' in hook
    assert "ReadRegStr $R0 HKCU" not in hook
    assert "StrCpy $R0 '$\\\"$R5\\unins000.exe$\\\"'" in hook
    assert 'GetFullPathName $R9 "$LOCALAPPDATA\\Programs\\RiskModelAgent\\."' in hook
    assert "${If} $R5 != $R9" in hook
    assert "${If} $R7 != $R0" in hook
    assert '${If} $R8 != "$R0 /SILENT"' in hook
    assert "ExecWait '$R0 /VERYSILENT /SUPPRESSMSGBOXES /NORESTART' $R1" in hook
    assert "${If} ${Errors}" in hook
    assert "${If} $R1 != 0" in hook
    assert hook.count("!insertmacro RMA_DETECT_LEGACY_INNO_KEY $R6") == 2
    assert hook.count("!insertmacro RMA_ABORT_LEGACY_MIGRATION") >= 5
    assert all(f"Push $R{register}" in hook for register in range(10))
    assert all(f"Pop $R{register}" in hook for register in range(10))
    assert "Abort" in hook


def test_legacy_migration_never_scans_or_deletes_user_data() -> None:
    hook = HOOK_PATH.read_text(encoding="utf-8")
    forbidden = (
        "EnumRegKey",
        "HKLM",
        "DeleteRegKey",
        "DeleteRegValue",
        "Delete ",
        "RMDir",
        "$LOCALAPPDATA\\RiskModelAgent",
        "workspace-selection.json",
    )

    assert not any(marker in hook for marker in forbidden)
