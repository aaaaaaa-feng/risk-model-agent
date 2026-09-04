from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
SMOKE_PATH = ROOT / "scripts" / "smoke_windows_tauri_installer.ps1"


def _terminal_gate(source: str) -> str:
    match = re.search(
        r"function Assert-NoVisibleBackendTerminal\s*\{[\s\S]*?"
        r"\n\}\n\nfunction Wait-ForDesktopWindow",
        source,
    )
    assert match is not None
    return match.group(0)


def test_terminal_gate_uses_one_process_snapshot_and_checks_process_identity() -> None:
    source = SMOKE_PATH.read_text(encoding="utf-8")
    gate = _terminal_gate(source)

    assert gate.count("Get-CimInstance Win32_Process") == 1
    assert "-ProcessSnapshot $ProcessSnapshot" in gate
    assert "$RootRuntime.StartTime.ToUniversalTime()" in gate
    assert "$DescendantProcess.StartTime.ToUniversalTime()" in gate
    assert "可能发生 PID 重用" in gate
    assert "$CurrentCreatedUtc -lt $ParentCreatedUtc" in source


def test_only_visible_conhost_windows_fail_with_bounded_safe_process_evidence() -> None:
    source = SMOKE_PATH.read_text(encoding="utf-8")
    gate = _terminal_gate(source)

    for marker in (
        "Initialize-NativeWindowProbe",
        "NativeWindowProbe]::Enumerate",
        "Where-Object { $_.Visible }",
        "$VisibleConsoleWindows.Count -gt 0",
        "pid=$($ConsoleHost.ProcessId)",
        "ppid=$($ConsoleHost.ParentProcessId)",
        "created=$SafeCreatedAt",
        "name=$SafeName",
        "exe=$SafeExecutable",
        "ancestors=$SafeAncestorChain",
        "Get-SafeProcessAncestorChain",
        "可见控制台窗口证据",
        "-MaximumLength 4000",
    ):
        assert marker in gate

    assert "桌面客户端进程树出现 conhost.exe" not in gate
    assert "GetFileName($ExecutablePath)" in source
    assert "$ProcessRow.CommandLine" not in source
    assert "$ConsoleHost.CommandLine" not in source
    assert "exe=$($ConsoleHost.ExecutablePath)" not in source


def test_recovery_runs_terminal_gate_immediately_before_full_model_smoke() -> None:
    source = SMOKE_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"Assert-NoVisibleBackendTerminal\s+`\s*\n"
        r"\s*-ClientProcessId \$ClientProcess\.Id\s+`\s*\n"
        r'\s*-Stage "恢复后-完整smoke前"\s*\n\s*'
        r'Write-Host "\[桌面冒烟\] 本地服务已就绪',
        source,
    )
