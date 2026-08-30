"""生成冻结目录与 Windows 安装包的可审计体积报告。"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Iterable


KIB = 1024
MIB = 1024 * KIB
BASELINE_INSTALLER_KIB = 239_176
MAX_INSTALLER_MIB = 180.0
MIN_REDUCTION_PERCENT = 25.0

# 这些能力不在桌面产品边界内，不应被重型依赖的可选 import 带入。
FORBIDDEN_COMPONENTS = (
    "polars",
    "_polars_runtime",
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
) -> dict[str, object]:
    selected_policy = policy or InstallerPolicy()
    violations = forbidden_paths(bundle) if bundle.is_dir() else []
    report: dict[str, object] = {
        "schema_version": "risk-package-size-report/v1",
        "bundle": directory_summary(bundle),
        "forbidden_components": {
            "passed": not violations,
            "paths": violations,
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
    report["valid"] = not violations and installer_passed
    return report


def write_report(report: dict[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="审计本地冻结目录与 Windows 安装包体积。")
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
        print("体积基线和上限必须大于 0。", file=sys.stderr)
        return 2
    if not 0 <= args.minimum_reduction_percent < 100:
        print("缩减比例必须位于 0 到 100 之间。", file=sys.stderr)
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
        print(f"打包体积审计失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"体积报告已生成：{args.output.resolve()}")
    if args.enforce and not report["valid"]:
        print("打包体积或依赖边界未通过门禁。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
