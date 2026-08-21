# Build with: python -m PyInstaller packaging/risk_model_agent.spec --noconfirm --clean
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


# PyInstaller exposes ``SPECPATH`` as the directory containing this spec.
# The repository root is therefore its direct parent; walking two levels up
# would silently point at the enclosing "8、新项目" folder.
ROOT = Path(SPECPATH).resolve().parent
datas = [
    (str(ROOT / "frontend" / "dist"), "frontend_dist"),
    # Exported model packages copy this standalone runtime as source. Python
    # modules otherwise live only inside PyInstaller's archive, so keep the file
    # at the path expected by ``app.workers.model_package``.
    (str(ROOT / "app" / "workers" / "package_runtime.py"), "app/workers"),
]
binaries = []


def runtime_submodule(name):
    """Exclude dependency test suites from the shipped application bundle."""
    return not ({"test", "tests", "testing"} & set(name.split(".")))


hiddenimports = [
    "openpyxl",
    "xlrd",
    "ipykernel_launcher",
    "skops.io",
    *collect_submodules("langgraph"),
    *collect_submodules("langgraph.checkpoint.sqlite"),
]
for package in (
    "xgboost", "lightgbm", "catboost", "skops", "polars", "duckdb", "debugpy",
):
    package_datas, package_binaries, package_hidden = collect_all(
        package,
        filter_submodules=runtime_submodule,
        exclude_datas=["**/test/**", "**/tests/**", "**/testing/**"],
    )
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hidden)

a = Analysis(
    [str(ROOT / "run_local.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    [],
    name="risk-model-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    exclude_binaries=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="risk-model-agent",
)
