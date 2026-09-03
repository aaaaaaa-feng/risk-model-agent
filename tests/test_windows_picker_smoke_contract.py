from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
SMOKE_PATH = ROOT / "scripts" / "smoke_windows_tauri_installer.ps1"


def _picker_function(source: str) -> str:
    match = re.search(
        r"function Invoke-NativePickerCancelSmoke\s*\{[\s\S]*?"
        r"\n\}\n\nfunction Wait-ForCurrentRunLogMarker",
        source,
    )
    assert match is not None
    return match.group(0)


def test_picker_cancel_smoke_enumerates_owned_dialog_instead_of_main_window() -> None:
    source = SMOKE_PATH.read_text(encoding="utf-8")
    picker = _picker_function(source)

    assert "EnumWindows" in source
    assert "GetWindowThreadProcessId" in source
    assert "IsWindowVisible" in source
    assert "RequestClose(long handle, int expectedProcessId)" in source
    assert "processId != (uint)expectedProcessId" in source
    assert 'string.Equals(className.ToString(), "#32770"' in source
    assert "if (!completed)" in source
    assert 'Where-Object { $_.Visible -and $_.ClassName -eq "#32770" }' in picker
    assert "NativeWindowProbe]::RequestClose" in picker
    assert "MainWindowHandle" not in picker
    assert "CloseMainWindow" not in picker


def test_picker_cancel_smoke_fails_closed_with_bounded_diagnostics() -> None:
    source = SMOKE_PATH.read_text(encoding="utf-8")
    picker = _picker_function(source)

    assert '$PickerJob.State -notin @("NotStarted", "Running")' in picker
    assert "Get-NativePickerDiagnostics" in picker
    assert "Get-Content -LiteralPath $BackendLogPath -Tail 20" in source
    assert "Get-DescendantProcesses -RootProcessId $BackendProcess.Id" in source
    assert "Get-NativeWindowSnapshots -ProcessIds" in source
    assert "-BackendLogPath $env:RISK_AGENT_BACKEND_LOG_PATH" in source
    assert "ConvertTo-DiagnosticFragment" in source
    assert "MaximumLength 2000" in source
    assert "cancelled -ne $true" in picker
