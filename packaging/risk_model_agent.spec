# Build with: python -m PyInstaller packaging/risk_model_agent.spec --noconfirm --clean
# PyInstaller 在执行 spec 时注入以下构建全局量。
# ruff: noqa: F821
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


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

hiddenimports = [
    "openpyxl",
    "xlrd",
    "duckdb",
    "ipykernel_launcher",
    "skops.io",
    # skops.io._persist 以字符串动态加载无 __init__.py 的旧协议兼容模块。
    "skops.io.old._general_v0",
    "skops.io.old._numpy_v0",
    "skops.io.old._numpy_v1",
    "xgboost.sklearn",
    "lightgbm.sklearn",
    "catboost.core",
    # 首方适配器通过稳定字符串路径延迟加载，显式列出以免后续静态调用变化漏收。
    "app.workers.model_builders",
    "app.notebooks.runtime",
    "app.packaging.self_test",
    "app.evaluation.adapter",
    "app.evaluation.harness",
    "app.evaluation.defaults",
    "app.evaluation.contracts",
    "app.evaluation.fakes",
    *collect_submodules("langgraph"),
    *collect_submodules("langgraph.checkpoint.sqlite"),
    # skops 通过序列化协议动态解析 io 子模块；仅收集模型包实际使用的 io 边界。
    *collect_submodules("skops.io"),
]

# XGBoost/LightGBM 使用 ctypes 加载原生库，静态分析无法发现，因此只显式
# 收集必需动态库和版本文件。CatBoost 的 _catboost 扩展由静态 import 自动收集。
binaries.extend(collect_dynamic_libs("xgboost"))
binaries.extend(collect_dynamic_libs("lightgbm"))
datas.extend(collect_data_files("xgboost", includes=["VERSION"]))
datas.extend(collect_data_files("lightgbm", includes=["VERSION.txt"]))

# 产品未提供分布式训练、调试器、代码补全或 Python 绘图能力。前端报告
# 图表由 Web 层渲染；Notebook 保留数据处理与逐单元执行能力。
excluded_modules = [
    "polars",
    "matplotlib",
    "plotly",
    "PIL",
    "graphviz",
    "dask",
    "distributed",
    "xgboost.dask",
    "xgboost.spark",
    "xgboost.testing",
    "lightgbm.dask",
    "lightgbm.plotting",
    "catboost.widget",
    "catboost.eval",
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
    "sklearn.datasets.tests",
    "pytest",
    "_pytest",
]


def without_test_data(entries):
    """移除第三方 hook 附带的测试样本，不影响正式运行时数据。"""
    markers = {"test", "tests", "testing"}
    return [
        entry
        for entry in entries
        if not (markers & {part.casefold() for part in Path(entry[0]).parts})
    ]

a = Analysis(
    [str(ROOT / "run_local.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
)
a.datas = without_test_data(a.datas)
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
