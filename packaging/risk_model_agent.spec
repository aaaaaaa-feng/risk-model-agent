# Build with: python -m PyInstaller packaging/risk_model_agent.spec --noconfirm --clean
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


# PyInstaller exposes ``SPECPATH`` as the directory containing this spec.
# The repository root is therefore its direct parent; walking two levels up
# would silently point at the enclosing "8、新项目" folder.
ROOT = Path(SPECPATH).resolve().parent
datas = [
    (str(ROOT / "app" / "templates"), "app/templates"),
    (str(ROOT / "app" / "static"), "app/static"),
]
hiddenimports = [
    "openpyxl",
    "xgboost",
    *collect_submodules("langgraph"),
]

a = Analysis(
    [str(ROOT / "run_local.py")],
    pathex=[str(ROOT)],
    binaries=[],
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
