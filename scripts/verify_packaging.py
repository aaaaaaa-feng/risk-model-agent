"""Check the cross-platform packaging contract without importing PyInstaller.

This is intentionally a source-level check. A real macOS/Windows build still
has to run on the target operating system with the optional package extra.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from verify_desktop_contract import build_contract as build_desktop_contract


ROOT = Path(__file__).resolve().parent.parent


def notebook_sources_are_removed(root: Path) -> bool:
    """递归检查 Notebook 及其代码生成入口已从源码树移除。"""

    notebook_package = root / "app" / "notebooks"
    retired_entrypoints = (
        root / "app" / "api" / "notebooks.py",
        root / "app" / "agents" / "codegen.py",
    )
    return not any(path.is_file() for path in notebook_package.rglob("*.py")) and not any(
        path.is_file() for path in retired_entrypoints
    )


def main() -> int:
    required_files = [
        "run_local.py",
        "packaging/risk_model_agent.spec",
        "scripts/build_mac.sh",
        "scripts/build_windows.ps1",
        "scripts/smoke_windows_service.ps1",
        "scripts/build_windows_tauri.ps1",
        "scripts/collect_tauri_installer.ps1",
        "scripts/smoke_windows_tauri_installer.ps1",
        "scripts/prepare_legacy_inno_fixture.ps1",
        "scripts/create_backend_manifest.py",
        "scripts/read_webview_cookie.py",
        "scripts/verify_desktop_contract.py",
        "scripts/smoke_packaged_service.py",
        "scripts/start_mac.command",
        "scripts/start_windows.ps1",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/src/App.tsx",
        "app/__init__.py",
        "app/api/capabilities.py",
        "app/bootstrap/context.py",
        "app/core/windows_process.py",
        "app/domain/pipeline.py",
        "app/governance/manifest.py",
        "app/governance/tracing.py",
        "app/orchestration/contracts.py",
        "app/orchestration/graph.py",
        "app/orchestration/process_runner.py",
        "app/services/pipeline_contracts.py",
        "app/workers/package_runtime.py",
        "app/workers/model_adapters.py",
        "app/workers/model_builders.py",
        "app/packaging/self_test.py",
        "app/evaluation/adapter.py",
        "app/evaluation/harness.py",
        "scripts/run_harness.py",
        "scripts/build_offline_bundle.py",
        "scripts/audit_package_size.py",
        ".github/workflows/package.yml",
        ".github/workflows/ci.yml",
    ]
    missing = [path for path in required_files if not (ROOT / path).is_file()]
    obsolete_current_release_files = [
        "packaging/windows_installer.iss",
        "packaging/languages/ChineseSimplified.isl",
        "packaging/languages/ChineseSimplified.LICENSE.txt",
        "scripts/compile_windows_installer.ps1",
        "scripts/smoke_windows_installer.ps1",
    ]
    spec = (
        (ROOT / "packaging/risk_model_agent.spec").read_text(encoding="utf-8")
        if not missing
        else ""
    )
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    frontend_package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    package_source = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
    main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    launcher_source = (ROOT / "run_local.py").read_text(encoding="utf-8")
    windows_process_source = (ROOT / "app/core/windows_process.py").read_text(encoding="utf-8")
    package_workflow = (
        (ROOT / ".github/workflows/package.yml").read_text(encoding="utf-8") if not missing else ""
    )
    windows_service_smoke = (
        (ROOT / "scripts/smoke_windows_service.ps1").read_text(encoding="utf-8")
        if not missing
        else ""
    )
    packaged_service_smoke = (
        (ROOT / "scripts/smoke_packaged_service.py").read_text(encoding="utf-8")
        if not missing
        else ""
    )
    package_audit_source = (
        (ROOT / "scripts/audit_package_size.py").read_text(encoding="utf-8") if not missing else ""
    )
    desktop_contract = build_desktop_contract(ROOT)
    project_version_match = re.search(
        r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', pyproject, re.MULTILINE
    )
    backend_version_match = re.search(
        r'^APP_VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$', main_source, re.MULTILINE
    )
    package_version_match = re.search(
        r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', package_source, re.MULTILINE
    )
    project_version = project_version_match.group(1) if project_version_match else ""
    backend_version = backend_version_match.group(1) if backend_version_match else ""
    package_version = package_version_match.group(1) if package_version_match else ""
    contract = {
        "schema_version": "risk-packaging-contract/v2",
        "required_files": len(required_files),
        "missing_files": missing,
        "legacy_current_release_build_path_is_absent": not any(
            (ROOT / path).exists() for path in obsolete_current_release_files
        ),
        "spec_has_onedir_collect": "COLLECT(" in spec,
        "spec_has_local_assets": "frontend_dist" in spec and "frontend" in spec,
        "spec_has_standalone_package_runtime": (
            '(str(ROOT / "app" / "workers" / "package_runtime.py"), "app/workers")' in spec
        ),
        "spec_has_model_and_data_capabilities": all(
            name in spec
            for name in (
                '"duckdb"',
                '"uvicorn.protocols.http.h11_impl"',
                '"uvicorn.loops.asyncio"',
                '"xgboost.sklearn"',
                '"lightgbm.sklearn"',
                '"catboost.core"',
                '"app.workers.model_builders"',
                '"app.packaging.self_test"',
                '"skops.io.old._general_v0"',
                '"skops.io.old._numpy_v0"',
                '"skops.io.old._numpy_v1"',
            )
        ),
        "spec_uses_precise_collection": "collect_all" not in spec
        and 'collect_dynamic_libs("xgboost")' in spec
        and 'collect_dynamic_libs("lightgbm")' in spec
        and 'collect_submodules("skops.io")' in spec,
        "spec_excludes_unused_modules": all(
            f'"{name}"' in spec
            for name in (
                "polars",
                "matplotlib",
                "plotly",
                "PIL",
                "dask",
                "distributed",
                "debugpy",
                "jedi",
                "uvloop",
                "watchfiles",
                "httptools",
                "tkinter",
                "IPython",
                "ipykernel",
                "jupyter_client",
                "jupyter_core",
                "nbformat",
                "notebook",
                "zmq",
            )
        ),
        "spec_excludes_optional_uvicorn_backends": all(
            f'"{name}"' in spec
            for name in (
                "uvicorn.loops.auto",
                "uvicorn.loops.uvloop",
                "uvicorn.protocols.http.auto",
                "uvicorn.protocols.http.httptools_impl",
            )
        ),
        "runtime_dependencies_are_slim": '"polars' not in pyproject.lower()
        and '"uvicorn[standard]' not in pyproject.lower()
        and '"httptools' not in pyproject.lower()
        and '"nbformat' not in pyproject.lower()
        and '"jupyter-client' not in pyproject.lower()
        and '"ipykernel' not in pyproject.lower()
        and '"duckdb' in pyproject.lower(),
        "server_uses_deterministic_uvicorn_backend": 'http="h11"' in main_source
        and 'loop="asyncio"' in main_source,
        "windows_backend_and_workers_are_windowless": (
            'console=sys.platform != "win32"' in spec
            and "_ensure_frozen_stdio" in launcher_source
            and "RISK_AGENT_BACKEND_LOG_PATH" in launcher_source
            and "install_frozen_windows_no_console_policy()" in launcher_source
            and "_winapi as module" in windows_process_source
            and "no_console_creation_flags(creation_flags)" in windows_process_source
            and "os_module.system = system_without_console" in windows_process_source
            and "shell=True, creationflags=CREATE_NO_WINDOW" in windows_process_source
        ),
        "xgboost_package_size_guard": '"xgboost>=2.0,<3.2"' in pyproject.lower(),
        "notebook_feature_is_removed": notebook_sources_are_removed(ROOT)
        and "ipykernel_launcher" not in launcher_source
        and "app.notebooks" not in spec
        and "app.api.notebooks" not in spec
        and "app.agents.codegen" not in spec
        and "/api/v1/notebooks" not in packaged_service_smoke,
        "package_audit_checks_embedded_python_modules": all(
            marker in package_audit_source
            for marker in (
                "CArchiveReader",
                'open_embedded_archive("PYZ.pyz")',
                "forbidden_module_names",
                '"embedded_modules": module_violations',
            )
        ),
        "launcher_supports_frozen_workers": "multiprocessing.freeze_support()" in launcher_source,
        "launcher_dispatches_package_self_test": "--internal-package-self-test" in launcher_source,
        "spec_has_launcher": "run_local.py" in spec,
        "spec_has_evaluation_adapter": "app.evaluation.adapter" in spec,
        "has_local_evaluation_harness": (ROOT / "app/evaluation/harness.py").is_file(),
        "has_offline_bundle_builder": (ROOT / "scripts/build_offline_bundle.py").is_file(),
        "spec_uses_repository_root": "ROOT = Path(SPECPATH).resolve().parent\n" in spec,
        "pyinstaller_optional_dependency": "pyinstaller" in pyproject.lower(),
        "versions_are_consistent": bool(project_version)
        and project_version == str(frontend_package.get("version") or "")
        and project_version == backend_version
        and project_version == package_version,
        "windows_runtime_smoke_isolated_and_fail_closed": all(
            value in windows_service_smoke
            for value in (
                "TcpListener",
                "-RedirectStandardOutput $RuntimeStdout",
                "-RedirectStandardError $RuntimeStderr",
                "WaitForExit",
                "Start-Process -FilePath $PythonExecutable",
                "$SmokeProcess.ExitCode -ne 0",
                "EvidenceOutputPath",
                "HttpParser",
                "Traceback",
            )
        )
        and all(
            value in packaged_service_smoke
            for value in (
                "--evidence-output",
                "risk-windows-migration-evidence/v1",
                "/api/v1/providers/settings",
                '"api_key_configured": False',
                "temporary_path.replace(evidence_path)",
            )
        ),
        "package_ci_has_size_gate": all(
            value in package_workflow
            for value in (
                "scripts/audit_package_size.py",
                "--baseline-kib 239176",
                "--maximum-mib 180",
                "--minimum-reduction-percent 25",
                "--enforce",
                "dist/installer/package-size-report.json",
            )
        ),
        "package_ci_runs_frozen_self_test": all(
            value in package_workflow
            for value in (
                "./dist/risk-model-agent/risk-model-agent --internal-package-self-test",
                "Start-Process",
                ".\\dist\\risk-model-agent\\risk-model-agent.exe",
                'ArgumentList @("--internal-package-self-test")',
                "$SelfTest.ExitCode",
            )
        ),
        "package_ci_uses_shared_windows_runtime_smoke": (
            ".\\scripts\\smoke_windows_service.ps1" in package_workflow
        ),
        "tauri_desktop_contract": desktop_contract,
        "tauri_desktop_contract_is_valid": bool(desktop_contract.get("valid")),
    }
    contract["valid"] = not missing and all(
        bool(contract[key])
        for key in (
            "spec_has_onedir_collect",
            "spec_has_local_assets",
            "spec_has_standalone_package_runtime",
            "spec_has_model_and_data_capabilities",
            "spec_uses_precise_collection",
            "spec_excludes_unused_modules",
            "spec_excludes_optional_uvicorn_backends",
            "runtime_dependencies_are_slim",
            "server_uses_deterministic_uvicorn_backend",
            "windows_backend_and_workers_are_windowless",
            "xgboost_package_size_guard",
            "notebook_feature_is_removed",
            "package_audit_checks_embedded_python_modules",
            "launcher_supports_frozen_workers",
            "launcher_dispatches_package_self_test",
            "spec_has_launcher",
            "spec_has_evaluation_adapter",
            "has_local_evaluation_harness",
            "has_offline_bundle_builder",
            "spec_uses_repository_root",
            "pyinstaller_optional_dependency",
            "versions_are_consistent",
            "legacy_current_release_build_path_is_absent",
            "windows_runtime_smoke_isolated_and_fail_closed",
            "package_ci_has_size_gate",
            "package_ci_runs_frozen_self_test",
            "package_ci_uses_shared_windows_runtime_smoke",
            "tauri_desktop_contract_is_valid",
        )
    )
    print(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True))
    if not contract["valid"]:
        print("packaging contract check failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
