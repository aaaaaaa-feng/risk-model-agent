"""静态核验 Tauri 桌面客户端与 Windows 发布链的安全契约。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tomllib


ROOT = Path(__file__).resolve().parent.parent


def _read(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def build_contract(root: Path = ROOT) -> dict[str, object]:
    required_files = [
        "desktop/package.json",
        "desktop/package-lock.json",
        "desktop/src-tauri/Cargo.toml",
        "desktop/src-tauri/Cargo.lock",
        "desktop/src-tauri/tauri.conf.json",
        "desktop/src-tauri/capabilities/splash-bootstrap.json",
        "desktop/src-tauri/src/backend.rs",
        "desktop/src-tauri/src/backend/logging.rs",
        "desktop/src-tauri/src/backend/process.rs",
        "desktop/src-tauri/src/backend/protocol.rs",
        "desktop/src-tauri/src/backend/window.rs",
        "desktop/src-tauri/src/commands.rs",
        "desktop/src-tauri/src/integrity.rs",
        "desktop/src-tauri/src/lib.rs",
        "desktop/src-tauri/src/main.rs",
        "desktop/src-tauri/build.rs",
        "desktop/src-tauri/windows/installer-hooks.nsh",
        "desktop/src-tauri/windows/verify-legacy-inno.ps1",
        "app/core/desktop_auth.py",
        "frontend/src/shared/lib/uiPreferences.ts",
        "scripts/build_windows_tauri.ps1",
        "scripts/collect_tauri_installer.ps1",
        "scripts/prepare_legacy_inno_fixture.ps1",
        "scripts/create_backend_manifest.py",
        "scripts/read_webview_cookie.py",
        "scripts/smoke_packaged_service.py",
        "scripts/smoke_windows_tauri_installer.ps1",
        ".github/workflows/ci.yml",
        ".github/workflows/package.yml",
    ]
    missing = [relative for relative in required_files if not (root / relative).is_file()]
    if missing:
        return {
            "schema_version": "risk-desktop-contract/v1",
            "missing_files": missing,
            "valid": False,
        }

    project = tomllib.loads(_read(root, "pyproject.toml"))
    desktop_package = json.loads(_read(root, "desktop/package.json"))
    desktop_lock = json.loads(_read(root, "desktop/package-lock.json"))
    cargo = tomllib.loads(_read(root, "desktop/src-tauri/Cargo.toml"))
    tauri_source = _read(root, "desktop/src-tauri/tauri.conf.json")
    tauri = json.loads(_read(root, "desktop/src-tauri/tauri.conf.json"))
    capability = json.loads(_read(root, "desktop/src-tauri/capabilities/splash-bootstrap.json"))
    # 桌面运行时按状态机、协议、进程、窗口与日志分层。静态合同必须
    # 覆盖这个完整边界，不能因为从单文件拆分就丢失安全门禁。
    backend = "\n".join(
        _read(root, relative)
        for relative in (
            "desktop/src-tauri/src/backend.rs",
            "desktop/src-tauri/src/backend/logging.rs",
            "desktop/src-tauri/src/backend/process.rs",
            "desktop/src-tauri/src/backend/protocol.rs",
            "desktop/src-tauri/src/backend/window.rs",
        )
    )
    desktop_lib = _read(root, "desktop/src-tauri/src/lib.rs")
    splash_source = _read(root, "desktop/src/splash.ts")
    app_main = _read(root, "app/main.py")
    desktop_auth_source = _read(root, "app/core/desktop_auth.py")
    notebook_runtime = _read(root, "app/notebooks/runtime.py")
    workspace_manager = _read(root, "app/core/workspace.py")
    workspace_paths = _read(root, "app/core/paths.py")
    packaging_spec = _read(root, "packaging/risk_model_agent.spec")
    launcher = _read(root, "run_local.py")
    ui_preferences = _read(root, "frontend/src/shared/lib/uiPreferences.ts")
    integrity = _read(root, "desktop/src-tauri/src/integrity.rs")
    rust_build = _read(root, "desktop/src-tauri/build.rs")
    rust_main = _read(root, "desktop/src-tauri/src/main.rs")
    cargo_source = _read(root, "desktop/src-tauri/Cargo.toml")
    package_source = _read(root, "desktop/package.json")
    build_script = _read(root, "scripts/build_windows_tauri.ps1")
    collect_script = _read(root, "scripts/collect_tauri_installer.ps1")
    smoke_script = _read(root, "scripts/smoke_windows_tauri_installer.ps1")
    legacy_fixture_script = _read(root, "scripts/prepare_legacy_inno_fixture.ps1")
    backend_manifest_script = _read(root, "scripts/create_backend_manifest.py")
    cookie_reader_script = _read(root, "scripts/read_webview_cookie.py")
    packaged_smoke_script = _read(root, "scripts/smoke_packaged_service.py")
    root_windows_build = _read(root, "scripts/build_windows.ps1")
    installer_hooks = _read(root, "desktop/src-tauri/windows/installer-hooks.nsh")
    legacy_hash_verifier = _read(root, "desktop/src-tauri/windows/verify-legacy-inno.ps1")
    package_workflow = _read(root, ".github/workflows/package.yml")
    ci_workflow = _read(root, ".github/workflows/ci.yml")
    windows_artifact_path = re.search(
        r"- name: Windows 10/11 x64[\s\S]*?artifact_path:\s*([^\r\n]+)",
        package_workflow,
    )
    smoke_process_launch = smoke_script.find("$SmokeProcess = Start-Process")
    cookie_clear_after_smoke = re.search(
        r"\$SmokeProcess = Start-Process[\s\S]*?finally\s*\{[\s\S]*?"
        r'"RISK_AGENT_SMOKE_DESKTOP_COOKIE",\s*\$null,',
        smoke_script,
    )
    forced_client_launch = smoke_script.find(
        "$ClientProcess = Start-Process",
        smoke_process_launch + 1,
    )
    obsolete_current_release_files = (
        "packaging/windows_installer.iss",
        "packaging/languages/ChineseSimplified.isl",
        "packaging/languages/ChineseSimplified.LICENSE.txt",
        "scripts/compile_windows_installer.ps1",
        "scripts/smoke_windows_installer.ps1",
    )

    app = tauri.get("app", {})
    bundle = tauri.get("bundle", {})
    windows_bundle = bundle.get("windows", {})
    webview_install = windows_bundle.get("webviewInstallMode", {})
    nsis = windows_bundle.get("nsis", {})
    resources = bundle.get("resources", {})
    windows = app.get("windows", [])
    window_by_label = {
        str(window.get("label")): window for window in windows if isinstance(window, dict)
    }
    permissions = set(capability.get("permissions", []))

    project_version = str(project.get("project", {}).get("version", ""))
    desktop_versions = {
        str(desktop_package.get("version", "")),
        str(desktop_lock.get("version", "")),
        str(desktop_lock.get("packages", {}).get("", {}).get("version", "")),
        str(cargo.get("package", {}).get("version", "")),
        str(tauri.get("version", "")),
    }

    contract: dict[str, object] = {
        "schema_version": "risk-desktop-contract/v1",
        "missing_files": [],
        "legacy_current_release_build_path_is_absent": not any(
            (root / relative).exists() for relative in obsolete_current_release_files
        ),
        "versions_are_consistent": len(desktop_versions) == 1
        and project_version in desktop_versions
        and bool(project_version),
        "tauri_bundles_only_nsis": bundle.get("active") is True
        and bundle.get("targets") == ["nsis"],
        "tauri_embeds_frozen_backend": resources.get("../../dist/risk-model-agent/") == "backend/",
        "tauri_installs_per_user": nsis.get("installMode") == "currentUser",
        "tauri_migrates_only_the_known_legacy_inno_install": (
            nsis.get("installerHooks") == "windows/installer-hooks.nsh"
            and "{4CE3329A-CF6F-49E0-86C7-BE5C38DB1474}_is1" in installer_hooks
            and '"DisplayName"' in installer_hooks
            and '"Publisher"' in installer_hooks
            and '"DisplayVersion"' in installer_hooks
            and '"InstallLocation"' in installer_hooks
            and '"UninstallString"' in installer_hooks
            and '"QuietUninstallString"' in installer_hooks
            and 'IfFileExists "$R5\\risk-model-agent.exe" rma_legacy_application_exists 0'
            in installer_hooks
            and 'IfFileExists "$R5\\unins000.exe" rma_legacy_uninstaller_exists 0'
            in installer_hooks
            and 'GetFullPathName $R9 "$LOCALAPPDATA\\Programs\\RiskModelAgent\\."'
            in installer_hooks
            and "${If} $R7 != $R0" in installer_hooks
            and '${If} $R8 != "$R0 /SILENT"' in installer_hooks
            and '${If} $R4 != "1.1.2"' in installer_hooks
            and not any(
                version in installer_hooks
                for version in ("1.0.0", "1.0.1", "1.0.2", "1.1.0", "1.1.1")
            )
            and "eed99b0776114cd7ff76c8fd0b6b6ab4b7dc7a6da7ac9c6e5f54b004e382e4df"
            in installer_hooks
            and "353e1ca0f6afcc8998cb50a55d9775279605b6ffa78f026f42d4c75daf22ab58"
            in installer_hooks
            and 'File "/oname=$PLUGINSDIR\\verify-legacy-inno.ps1"' in installer_hooks
            and 'IfFileExists "$PLUGINSDIR\\verify-legacy-inno.ps1" rma_legacy_hash_script_exists 0'
            in installer_hooks
            and "+2 0\n      !insertmacro RMA_ABORT_LEGACY_MIGRATION" not in installer_hooks
            and '"$SYSDIR\\WindowsPowerShell\\v1.0\\powershell.exe"' in installer_hooks
            and "nsExec::Exec /TIMEOUT=60000" in installer_hooks
            and "nsExec::ExecToStack" not in installer_hooks
            and 'IfFileExists "$R5\\risk-model-agent.exe" 0 rma_legacy_application_removed'
            in installer_hooks
            and "SHA256]::Create()" in legacy_hash_verifier
            and "ComputeHash($Stream)" in legacy_hash_verifier
            and legacy_hash_verifier.count('ValidatePattern("^[0-9a-fA-F]{64}$")') == 2
            and "Get-FileHash" not in legacy_hash_verifier
            and "ReadAllBytes" not in legacy_hash_verifier
            and "Write-Host" not in legacy_hash_verifier
            and "Write-Output" not in legacy_hash_verifier
            and "ReadRegStr $R0 HKCU" not in installer_hooks
            and "ExecWait" in installer_hooks
            and "Abort" in installer_hooks
            and "Delete " not in installer_hooks
            and "RMDir " not in installer_hooks
            and "%LOCALAPPDATA%\\RiskModelAgent" not in installer_hooks
        ),
        "tauri_uses_small_webview_bootstrapper": webview_install.get("type")
        == "downloadBootstrapper",
        "tauri_has_startup_and_main_windows": (
            window_by_label.get("splash", {}).get("visible") is True
            and window_by_label.get("splash", {}).get("url") == "index.html"
            and window_by_label.get("main", {}).get("visible") is False
            and window_by_label.get("main", {}).get("url") == "main.html"
        ),
        "tauri_main_window_fits_common_scaled_laptops": (
            float(window_by_label.get("main", {}).get("minWidth", 9999)) <= 680
            and float(window_by_label.get("main", {}).get("minHeight", 9999)) <= 360
            and float(window_by_label.get("splash", {}).get("width", 9999)) <= 560
            and float(window_by_label.get("splash", {}).get("height", 9999)) <= 360
            and "fitted_main_window_size" in backend
            and "current_monitor" in backend
            and "scale_factor" in backend
            and "1.75" in backend
        ),
        "tauri_transitions_from_splash_to_embedded_main": all(
            marker in backend
            for marker in (
                'get_webview_window("main")',
                "main.navigate(url)",
                "main.show()",
                'get_webview_window("splash")',
                ".hide()",
            )
        ),
        "tauri_recovers_after_runtime_backend_exit": all(
            marker in backend
            for marker in (
                "monitor_ready_backend",
                "handle_runtime_failure",
                "show_recovery_window",
                "本地服务运行中断",
                "runtime backend failure:",
            )
        )
        and 'if (status.phase === "ready")' not in splash_source
        and all(
            marker in desktop_lib
            for marker in (
                "tauri_plugin_single_instance::init",
                "is_restartable",
                "supervisor.launch(app.clone())",
            )
        ),
        "tauri_closes_hidden_splash_with_main_window": all(
            marker in desktop_lib
            for marker in (
                "WindowEvent::CloseRequested",
                'label == "main" || label == "splash"',
                "app_handle.exit(0)",
                "RunEvent::Exit",
            )
        ),
        "tauri_ipc_is_scoped_to_local_splash": (
            capability.get("local") is True
            and capability.get("windows") == ["splash"]
            and app.get("security", {}).get("capabilities") == ["splash-bootstrap"]
            and {
                "allow-backend-status",
                "allow-retry-backend",
                "allow-open-log-directory",
            }.issubset(permissions)
        ),
        "tauri_does_not_expose_shell_plugin": "tauri-plugin-shell" not in cargo_source
        and "@tauri-apps/plugin-shell" not in package_source,
        "windows_client_uses_gui_subsystem": (
            'windows_subsystem = "windows"' in rust_main and "not(debug_assertions)" in rust_main
        ),
        "backend_is_loopback_and_never_opens_browser": (
            'const BACKEND_HOST: &str = "127.0.0.1"' in backend
            and '.env("RISK_AGENT_OPEN_BROWSER", "0")' in backend
            and "TcpListener::bind" in backend
        ),
        "backend_startup_is_instance_authenticated": (
            "RISK_AGENT_DESKTOP_TOKEN" in backend
            and "X-Risk-Agent-Desktop-Token" in backend
            and "/api/v1/desktop/ready" in backend
        ),
        "desktop_webview_bootstrap_is_one_use_and_not_logged": all(
            marker in backend
            for marker in (
                "RISK_AGENT_DESKTOP_BOOTSTRAP_TOKEN",
                "bootstrap_token = generate_desktop_token()",
                "desktop_bootstrap_url",
                "/api/v1/desktop/bootstrap",
                "Do not attach the",
            )
        )
        and all(
            marker in desktop_auth_source
            for marker in (
                "DESKTOP_BOOTSTRAP_TOKEN_ENV",
                "os.environ.pop(DESKTOP_TOKEN_ENV",
                "os.environ.pop(DESKTOP_BOOTSTRAP_TOKEN_ENV",
                "self._bootstrap_token = None",
                'RedirectResponse(url="/", status_code=303)',
                "httponly=True",
                'samesite="strict"',
            )
        )
        and all(
            marker in app_main
            for marker in (
                '@application.get("/api/v1/desktop/bootstrap"',
                "return desktop_auth.bootstrap_response(request)",
                'access_log=not bool(getattr(app.state, "desktop_mode", False))',
            )
        ),
        "desktop_business_api_requires_bootstrap_cookie": all(
            marker in desktop_auth_source
            for marker in (
                "DESKTOP_SESSION_COOKIE",
                "DESKTOP_PUBLIC_PATHS",
                "request.url.path in DESKTOP_PUBLIC_PATHS",
                "DESKTOP_SESSION_REQUIRED",
                '"/api/v1/desktop/shutdown"',
            )
        )
        and "desktop_auth.business_session_error(request)" in app_main
        and '"/api/v1/session"' in app_main,
        "desktop_public_health_is_minimal": all(
            marker in app_main
            for marker in (
                "if desktop_auth.enabled:",
                "installer smoke before the WebView cookie exists",
                '"data_directory": str(active_context.paths.root)',
            )
        )
        and all(
            marker in desktop_auth_source
            for marker in (
                "def minimal_health",
                '"desktop": True',
            )
        )
        and '"data_directory"' not in desktop_auth_source,
        "backend_shutdown_is_authenticated_and_graceful": all(
            marker in backend
            for marker in (
                "/api/v1/desktop/shutdown",
                "graceful_stop_child",
                "GRACEFUL_SHUTDOWN_TIMEOUT",
                "backend stopped gracefully",
            )
        )
        and all(
            marker in app_main
            for marker in (
                '@application.post("/api/v1/desktop/shutdown"',
                "desktop_shutdown_callback",
                "server.should_exit = True",
                "desktop graceful shutdown completed",
            )
        ),
        "backend_process_is_hidden_and_owned": all(
            marker in backend
            for marker in (
                "CREATE_NO_WINDOW",
                "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
                "SetInformationJobObject",
                "AssignProcessToJobObject",
                ".stdout(Stdio::from(log_file))",
                ".stderr(Stdio::from(stderr_log))",
                "RISK_AGENT_BACKEND_LOG_PATH",
            )
        )
        and 'console=sys.platform != "win32"' in packaging_spec
        and "_ensure_frozen_stdio" in launcher
        and 'return {"creationflags": 0x0800_0000}' in notebook_runtime,
        "ui_preferences_survive_random_loopback_ports": all(
            marker in ui_preferences
            for marker in (
                "rma_ui_",
                "SameSite=Strict",
                "readUiPreference",
                "writeUiPreference",
                "localStorage",
            )
        ),
        "workspace_cannot_live_inside_installation": (
            "RISK_AGENT_INSTALL_DIR" in backend
            and "validate_workspace_root" in workspace_manager
            and "RISK_AGENT_INSTALL_DIR" in workspace_paths
            and "WORKSPACE_PATH_INSIDE_INSTALLATION" in workspace_paths
            and "root = validate_workspace_root(explicit_data_dir)" in workspace_paths
            and "selected = validate_workspace_root(explicit_workspace)" in workspace_paths
            and "candidate = validate_workspace_root(raw_candidate)" in workspace_paths
        ),
        "backend_manifest_is_complete_and_build_time_pinned": all(
            marker in backend_manifest_script
            for marker in (
                "risk-model-agent/backend-manifest/v1",
                "sha256",
                "follow_symlinks=False",
                "os.replace",
            )
        )
        and all(
            marker in rust_build
            for marker in (
                "backend-manifest.json",
                "Sha256::digest",
                "RISK_AGENT_BACKEND_MANIFEST_SHA256",
            )
        )
        and all(
            marker in integrity
            for marker in (
                "RISK_AGENT_BACKEND_MANIFEST_SHA256",
                "verify_backend_bundle",
                "canonicalize",
                "sha256_file",
                "MANIFEST_SCHEMA",
            )
        ),
        "build_script_runs_all_desktop_gates": all(
            marker in build_script
            for marker in (
                "$Npm ci",
                "$Npm run typecheck",
                "$Npm run build",
                "$Cargo fmt",
                "$Cargo test",
                "$Cargo clippy",
                "-D warnings",
                "$Npm run tauri",
                "RISK_AGENT_BACKEND_MANIFEST",
                "IsPathFullyQualified",
                "backend-manifest.json",
                "--bundles",
                "nsis",
                "--ci",
            )
        ),
        "collector_is_unique_hashed_and_tauri_identified": all(
            marker in collect_script
            for marker in (
                "RiskModelAgent-$Version-windows-x64-setup.exe",
                "Get-FileHash",
                "SHA256",
                "risk-tauri-installer/v1",
                "tauri-nsis",
                "Count -ne 1",
            )
        ),
        "legacy_inno_fixture_is_isolated_from_formal_artifacts": all(
            marker in legacy_fixture_script
            for marker in (
                "1.1.2",
                "releases/download/1.1.2",
                "b0d3ce62632a95ffd72e76ac27c49727af11d856ee74d22586190b5efaf27636",
                "Invoke-WebRequest",
                "dist\\fixtures\\legacy-inno",
                "risk-legacy-inno-fixture/v1",
                "formal_release_artifact = $false",
                "UnexpectedFormalExe.Count -ne 0",
            )
        ),
        "installed_client_smoke_is_fail_closed": all(
            marker in smoke_script
            for marker in (
                "Win32_Process",
                "ParentProcessId",
                "Get-NetTCPConnection",
                "smoke_packaged_service.py",
                "MainWindowHandle",
                "RISK_AGENT_OPEN_BROWSER",
                "taskkill.exe",
                "must-survive-uninstall.txt",
                "migration-must-survive",
                "LegacyRegistryPath",
                "b0d3ce62632a95ffd72e76ac27c49727af11d856ee74d22586190b5efaf27636",
                "Traceback",
                "$env:LOCALAPPDATA",
                "com.feng.riskmodelagent\\logs",
                "$ClientStartedAt",
                "LastWriteTimeUtc",
                "无法在 20 秒内回收进程",
                "Find-UniqueUninstaller",
                "package-size-report.json",
                "risk-package-size-report/v1",
                "UnexpectedArtifactFiles",
                "Test-Path $LegacyUninstaller",
                "Join-Path $env:LOCALAPPDATA $ProductName",
                "New-Item -Path $LegacyRegistryPath",
                "Assert-RejectedTauriMigration",
                "$UnexpectedInstallItems.Count -gt 0",
                "Remove-Item -LiteralPath $InstallDirectory -Force",
                "$ExpectedLegacyExecutableHash",
                "$ExpectedLegacyUninstallerHash",
                "没有完整性白名单的 1.1.1 旧版本",
                "真实 1.1.2 主程序被替换",
                "真实 1.1.2 卸载器被替换",
                "Get-ProductShortcutFingerprint",
                "CommonPrograms",
                "$CleanupLegacyUninstallerHash",
                'New-ItemProperty -Path $LegacyRegistryPath -Name "DisplayName"',
                'New-ItemProperty -Path $LegacyRegistryPath -Name "Publisher"',
                'New-ItemProperty -Path $LegacyRegistryPath -Name "InstallLocation"',
                "Get-ProductUninstallEntries",
                "if (-not (Test-Path $RegistryRoot -PathType Container))",
                "Get-ProductShortcuts",
                "Assert-NoVisibleBackendTerminal",
                "Wait-ForDesktopWindow",
                "Start-Process -FilePath $PythonExecutable",
                "CloseMainWindow",
                "risk-windows-migration-evidence/v1",
                "EvidenceOutputPath",
                "needs_setup",
                "Invoke-NativePickerCancelSmoke",
                "/api/v1/workspace/native-picker",
                "MainWindowHandle -ne 0",
                "cancelled -ne $true",
                "/api/v1/workspace/select",
                "runtime backend failure:",
                "$RecoveryTriggerProcess",
                "RejectedProcessId",
                "desktop graceful shutdown completed",
                "backend stopped gracefully",
                "Stop-Process -Id $ClientProcess.Id -Force",
                "Job Object",
            )
        ),
        "installed_smoke_uses_real_webview_session_without_production_bypass": (
            all(
                marker in smoke_script
                for marker in (
                    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                    "--remote-debugging-address=127.0.0.1",
                    "--remote-debugging-port=$WebViewDebugPort",
                    "read_webview_cookie.py",
                    "New-AuthenticatedDesktopWebSession",
                    "Microsoft.PowerShell.Commands.WebRequestSession",
                    "RISK_AGENT_SMOKE_DESKTOP_COOKIE",
                    "$OriginalWebViewArguments",
                    "$OriginalSmokeDesktopCookie",
                )
            )
            and "remote-allow-origins" not in smoke_script
            and "Write-Host $DesktopSessionCookie" not in smoke_script
            and "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS" not in backend
            and "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS" not in desktop_lib
            and "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS" not in package_workflow
            and "remote-debugging-port" not in tauri_source
            and "remote-debugging-port" not in backend
            and "remote-debugging-port" not in desktop_lib
            and "remote-debugging-port" not in rust_main
            and cookie_clear_after_smoke is not None
            and smoke_process_launch >= 0
            and forced_client_launch > cookie_clear_after_smoke.end()
        ),
        "webview_cookie_reader_is_loopback_scoped_and_http_only": all(
            marker in cookie_reader_script
            for marker in (
                'parsed.hostname != "127.0.0.1"',
                'endpoint = f"http://127.0.0.1:{debug_port}/json/list"',
                '"method": "Network.getCookies"',
                '"params": {"urls": [f"{backend_url}/"]}',
                'cookie.get("httpOnly") is True',
                'domain.lstrip(".") == "127.0.0.1"',
                "COOKIE_VALUE_PATTERN.fullmatch(value)",
            )
        )
        and "Network.getAllCookies" not in cookie_reader_script,
        "packaged_smoke_cookie_transport_is_explicit_and_ci_scoped": all(
            marker in packaged_smoke_script
            for marker in (
                'DESKTOP_COOKIE_ENV = "RISK_AGENT_SMOKE_DESKTOP_COOKIE"',
                'DESKTOP_COOKIE_NAME = "risk_agent_desktop_session"',
                "DESKTOP_COOKIE_PATTERN.fullmatch(value)",
                'return {"Cookie": f"{DESKTOP_COOKIE_NAME}={value}"}',
                'if health.get("desktop") is True:',
            )
        )
        and "websockets>=14,<17"
        in str(project.get("project", {}).get("optional-dependencies", {}).get("package", [])),
        "package_ci_builds_tauri_only_for_formal_windows_artifact": all(
            marker in package_workflow
            for marker in (
                "scripts\\build_windows_tauri.ps1",
                "scripts\\collect_tauri_installer.ps1",
                "scripts\\smoke_windows_tauri_installer.ps1",
                "scripts\\prepare_legacy_inno_fixture.ps1",
                "dist/installer",
                "Swatinem/rust-cache",
                "desktop/**",
            )
        )
        and "scripts\\compile_windows_installer.ps1" not in package_workflow
        and "scripts\\smoke_windows_installer.ps1" not in package_workflow
        and bool(windows_artifact_path)
        and windows_artifact_path.group(1).strip() == "dist/installer",
        "package_ci_checks_existing_frontend_before_freezing": all(
            marker in package_workflow
            for marker in (
                "npm run typecheck",
                "npm run lint",
                "npm test",
                "npm run build",
                "python -m PyInstaller",
            )
        )
        and package_workflow.index("npm run build")
        < package_workflow.index("python -m PyInstaller"),
        "package_ci_pins_backend_manifest_before_tauri_build": (
            "python scripts/create_backend_manifest.py" in package_workflow
            and package_workflow.index("python -m PyInstaller")
            < package_workflow.index("python scripts/create_backend_manifest.py")
            < package_workflow.index("scripts\\build_windows_tauri.ps1")
        ),
        "root_windows_build_uses_only_tauri_release_path": all(
            marker in root_windows_build
            for marker in (
                "scripts\\verify_packaging.py",
                "scripts\\verify_desktop_contract.py",
                "-m PyInstaller",
                "scripts\\create_backend_manifest.py",
                "--internal-package-self-test",
                "scripts\\build_windows_tauri.ps1",
                "scripts\\collect_tauri_installer.ps1",
            )
        )
        and "compile_windows_installer.ps1" not in root_windows_build
        and root_windows_build.index("-m PyInstaller")
        < root_windows_build.index("scripts\\create_backend_manifest.py")
        < root_windows_build.index("--internal-package-self-test")
        < root_windows_build.index("scripts\\build_windows_tauri.ps1")
        < root_windows_build.index("scripts\\collect_tauri_installer.ps1"),
        "ci_checks_desktop_typescript_and_rust": all(
            marker in ci_workflow
            for marker in (
                "desktop/package-lock.json",
                "npm run typecheck",
                "cargo fmt",
                "cargo test",
                "cargo clippy",
                "Swatinem/rust-cache",
                "python scripts/create_backend_manifest.py",
            )
        ),
    }
    contract["valid"] = all(
        bool(value)
        for key, value in contract.items()
        if key not in {"schema_version", "missing_files"}
    )
    return contract


def main() -> int:
    try:
        contract = build_contract()
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f"Tauri 桌面契约核验失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True))
    if not contract.get("valid"):
        print("Tauri 桌面契约不完整，已阻止发布。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
