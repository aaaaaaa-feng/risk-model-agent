"""生成冻结目录与 Windows 安装包的可审计体积报告。"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Iterable, TextIO


KIB = 1024
MIB = 1024 * KIB
BASELINE_INSTALLER_KIB = 239_176
MAX_INSTALLER_MIB = 180.0
MIN_REDUCTION_PERCENT = 25.0

# 这些能力不在桌面产品边界内，不应被重型依赖的可选 import 带入。
FORBIDDEN_COMPONENTS = (
    "polars",
    "_polars_runtime",
    "ipython",
    "ipykernel",
    "ipykernel_launcher",
    "jupyter",
    "jupyter_client",
    "jupyter_core",
    "nbformat",
    "notebook",
    "prompt_toolkit",
    "traitlets",
    "tornado",
    "zmq",
    "matplotlib",
    "plotly",
    "pil",
    "graphviz",
    "dask",
    "distributed",
    "debugpy",
    "_pydevd_bundle",
    "pydevd",
    "jedi",
    "parso",
    "uvloop",
    "watchfiles",
    "httptools",
    "tkinter",
    "_tkinter",
    "_tcl_data",
    "_tk_data",
    "tcl",
)
TEST_COMPONENTS = {"test", "tests", "testing", "_pytest", "pytest"}
FORBIDDEN_MODULE_PREFIXES = (
    "app.notebooks",
    "app.api.notebooks",
    "app.agents.codegen",
    "xgboost.dask",
    "xgboost.spark",
    "xgboost.testing",
    "lightgbm.dask",
    "lightgbm.plotting",
    "catboost.widget",
    "catboost.eval",
    "uvicorn.loops.auto",
    "uvicorn.loops.uvloop",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.httptools_impl",
)


class PackageAuditError(ValueError):
    """打包审计输入不完整或不唯一。"""


@dataclass(frozen=True)
class InstallerPolicy:
    baseline_kib: int = BASELINE_INSTALLER_KIB
    maximum_mib: float = MAX_INSTALLER_MIB
    minimum_reduction_percent: float = MIN_REDUCTION_PERCENT

    @property
    def baseline_bytes(self) -> int:
        return self.baseline_kib * KIB

    @property
    def maximum_bytes(self) -> int:
        return int(self.maximum_mib * MIB)


def _files(root: Path) -> Iterable[Path]:
    return (path for path in root.rglob("*") if path.is_file())


def _size(path: Path) -> int:
    return path.stat().st_size


def _component_name(relative: Path) -> str:
    parts = relative.parts
    if not parts:
        return "."
    if parts[0].casefold() == "_internal" and len(parts) > 1:
        return parts[1]
    return parts[0]


def _matches_component(part: str, marker: str) -> bool:
    value = part.casefold()
    expected = marker.casefold()
    return (
        value == expected
        or value.startswith(f"{expected}.")
        or value.startswith(f"{expected}-")
        or value.startswith(f"{expected}_")
    )


def forbidden_paths(bundle: Path) -> list[str]:
    violations: set[str] = set()
    for path in _files(bundle):
        relative = path.relative_to(bundle)
        for part in relative.parts:
            lowered = part.casefold()
            if lowered in TEST_COMPONENTS or any(
                _matches_component(part, marker) for marker in FORBIDDEN_COMPONENTS
            ):
                violations.add(relative.as_posix())
                break
    return sorted(violations)


def embedded_python_modules(bundle: Path) -> tuple[str, ...]:
    """读取 PyInstaller 主程序及其内嵌 PYZ 的模块名。"""

    candidates = [bundle / "risk-model-agent.exe", bundle / "risk-model-agent"]
    executables = [path for path in candidates if path.is_file()]
    if len(executables) != 1:
        raise PackageAuditError(
            f"PyInstaller 主程序必须唯一，当前找到 {len(executables)} 个：{bundle}"
        )
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        raise PackageAuditError("检查内嵌 Python 模块需要安装 PyInstaller 打包依赖。") from exc
    try:
        archive = CArchiveReader(str(executables[0]))
        names = set(archive.toc)
        names.update(archive.open_embedded_archive("PYZ.pyz").toc)
    except Exception as exc:
        raise PackageAuditError("无法读取 PyInstaller 主程序的内嵌模块清单。") from exc
    return tuple(sorted(str(name) for name in names))


def forbidden_module_names(module_names: Iterable[str]) -> list[str]:
    violations: set[str] = set()
    for module_name in module_names:
        normalized = module_name.replace("/", ".").casefold()
        top_level = normalized.split(".", 1)[0]
        forbidden_top_level = (
            any(_matches_component(top_level, marker) for marker in FORBIDDEN_COMPONENTS)
            or top_level in TEST_COMPONENTS
        )
        forbidden_nested = any(
            normalized == prefix.casefold() or normalized.startswith(f"{prefix.casefold()}.")
            for prefix in FORBIDDEN_MODULE_PREFIXES
        )
        if forbidden_top_level or forbidden_nested:
            violations.add(module_name)
    return sorted(violations)


def directory_summary(bundle: Path, *, top: int = 30) -> dict[str, object]:
    if not bundle.is_dir():
        raise PackageAuditError(f"找不到 PyInstaller 目录：{bundle}")
    components: dict[str, int] = defaultdict(int)
    total_bytes = 0
    file_count = 0
    for path in _files(bundle):
        size = _size(path)
        total_bytes += size
        file_count += 1
        components[_component_name(path.relative_to(bundle))] += size
    largest = sorted(components.items(), key=lambda item: (-item[1], item[0]))[:top]
    return {
        "path": str(bundle.resolve()),
        "bytes": total_bytes,
        "mib": round(total_bytes / MIB, 3),
        "file_count": file_count,
        "largest_components": [
            {"name": name, "bytes": size, "mib": round(size / MIB, 3)} for name, size in largest
        ],
    }


def resolve_installer(path: Path) -> Path:
    if path.is_file():
        return path
    if not path.is_dir():
        raise PackageAuditError(f"找不到 Windows 安装包：{path}")
    installers = sorted(path.glob("RiskModelAgent-*-windows-x64-setup.exe"))
    if len(installers) != 1:
        raise PackageAuditError(f"Windows 安装包必须唯一，当前找到 {len(installers)} 个：{path}")
    return installers[0]


def installer_summary(installer: Path, policy: InstallerPolicy) -> dict[str, object]:
    size = _size(installer)
    reduction = (1 - size / policy.baseline_bytes) * 100
    return {
        "path": str(installer.resolve()),
        "bytes": size,
        "kib": round(size / KIB, 3),
        "mib": round(size / MIB, 3),
        "baseline_kib": policy.baseline_kib,
        "reduction_percent": round(reduction, 3),
        "maximum_mib": policy.maximum_mib,
        "minimum_reduction_percent": policy.minimum_reduction_percent,
        "within_maximum": size <= policy.maximum_bytes,
        "meets_reduction": reduction >= policy.minimum_reduction_percent,
    }


def create_report(
    bundle: Path,
    *,
    installer: Path | None = None,
    policy: InstallerPolicy | None = None,
    embedded_modules: Iterable[str] | None = None,
) -> dict[str, object]:
    selected_policy = policy or InstallerPolicy()
    bundle_summary = directory_summary(bundle)
    path_violations = forbidden_paths(bundle)
    module_names = (
        tuple(embedded_modules) if embedded_modules is not None else embedded_python_modules(bundle)
    )
    module_violations = forbidden_module_names(module_names)
    report: dict[str, object] = {
        "schema_version": "risk-package-size-report/v1",
        "bundle": bundle_summary,
        "forbidden_components": {
            "passed": not path_violations and not module_violations,
            "paths": path_violations,
            "embedded_modules": module_violations,
            "embedded_module_count": len(module_names),
        },
        "policy": {
            "baseline_installer_kib": selected_policy.baseline_kib,
            "maximum_installer_mib": selected_policy.maximum_mib,
            "minimum_reduction_percent": selected_policy.minimum_reduction_percent,
        },
    }
    installer_passed = True
    if installer is not None:
        summary = installer_summary(resolve_installer(installer), selected_policy)
        report["installer"] = summary
        installer_passed = bool(summary["within_maximum"] and summary["meets_reduction"])
    report["valid"] = not path_violations and not module_violations and installer_passed
    return report


def write_report(report: dict[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)


def _console_safe_text(value: str, stream: TextIO) -> str:
    """按当前控制台编码降级文本，避免 Windows 旧代码页直接崩溃。"""

    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        value.encode(encoding)
    except LookupError:
        encoding = "ascii"
    except UnicodeEncodeError:
        pass
    else:
        return value
    return value.encode(encoding, errors="backslashreplace").decode(encoding)


def _print_console(value: str, *, stream: TextIO) -> None:
    print(_console_safe_text(value, stream), file=stream)


class _ConsoleSafeArgumentParser(argparse.ArgumentParser):
    """让帮助和参数错误也遵守同一控制台编码边界。"""

    def _print_message(self, message: str, file: TextIO | None = None) -> None:
        if message:
            stream = file or sys.stderr
            stream.write(_console_safe_text(message, stream))


def _parser() -> argparse.ArgumentParser:
    parser = _ConsoleSafeArgumentParser(description="审计本地冻结目录与 Windows 安装包体积。")
    parser.add_argument("--bundle", type=Path, default=Path("dist/risk-model-agent"))
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--output", type=Path, default=Path("dist/package-size-report.json"))
    parser.add_argument("--baseline-kib", type=int, default=BASELINE_INSTALLER_KIB)
    parser.add_argument("--maximum-mib", type=float, default=MAX_INSTALLER_MIB)
    parser.add_argument("--minimum-reduction-percent", type=float, default=MIN_REDUCTION_PERCENT)
    parser.add_argument("--enforce", action="store_true", help="门禁失败时返回非零状态码。")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.baseline_kib <= 0 or args.maximum_mib <= 0:
        _print_console("体积基线和上限必须大于 0。", stream=sys.stderr)
        return 2
    if not 0 <= args.minimum_reduction_percent < 100:
        _print_console("缩减比例必须位于 0 到 100 之间。", stream=sys.stderr)
        return 2
    try:
        report = create_report(
            args.bundle,
            installer=args.installer,
            policy=InstallerPolicy(
                baseline_kib=args.baseline_kib,
                maximum_mib=args.maximum_mib,
                minimum_reduction_percent=args.minimum_reduction_percent,
            ),
        )
        write_report(report, args.output)
    except (OSError, PackageAuditError) as exc:
        _print_console(f"打包体积审计失败：{exc}", stream=sys.stderr)
        return 2
    # 控制台 JSON 使用 ASCII 转义保持可解析；UTF-8 报告文件仍保留原始中文。
    _print_console(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True),
        stream=sys.stdout,
    )
    _print_console(f"体积报告已生成：{args.output.resolve()}", stream=sys.stdout)
    if args.enforce and not report["valid"]:
        _print_console("打包体积或依赖边界未通过门禁。", stream=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
