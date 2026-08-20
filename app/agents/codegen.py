from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Sequence


FORBIDDEN_IMPORTS = {
    "requests", "httpx", "urllib", "socket", "aiohttp", "ftplib", "paramiko",
    "subprocess", "pickle", "cloudpickle", "dill",
}
FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "__import__", "system", "popen", "open",
    "unlink", "remove", "rmdir", "rmtree", "rename", "replace",
    "read_bytes", "read_text", "write_bytes", "write_text",
}
ALLOWED_IMPORTS = {"app", "json", "numpy", "pandas", "pathlib"}


def generate_reproducible_notebook_code(
    *,
    dataset_file: str,
    target: str,
    features: Sequence[str],
    split: dict[str, Any],
    models: Sequence[str],
    score_config: dict[str, Any],
) -> str:
    specification = {
        "dataset_file": dataset_file,
        "target": target,
        "features": list(features),
        "split": {
            "method": split.get("method"),
            "time_column": split.get("time_column"),
            "customer_key": split.get("customer_key"),
            "random_state": split.get("random_state", 42),
        },
        "models": list(models),
        "score": score_config,
    }
    encoded = json.dumps(specification, ensure_ascii=False, indent=2)
    return f'''from pathlib import Path
import json
import pandas as pd

from app.workers.io import plan_resources, read_table
from app.workers.modeling import train_candidates
from app.workers.screening import screen_features
from app.workers.splitting import freeze_target_samples, split_dataset

SPEC = json.loads(r"""{encoded}""")
DATASET = Path(SPEC["dataset_file"])

# 所有筛选、分箱和拟合仅使用 Train；OOT 不参与选择。
source = read_table(DATASET)
frame, target_evidence = freeze_target_samples(source, SPEC["target"])
split = split_dataset(
    frame,
    SPEC["target"],
    method=SPEC["split"]["method"],
    time_column=SPEC["split"]["time_column"],
    customer_key=SPEC["split"]["customer_key"],
    random_state=SPEC["split"]["random_state"],
)
train = frame.iloc[split["indices"]["train"]]
screening = screen_features(train, SPEC["target"], SPEC["features"])
resource = plan_resources(len(frame), len(frame.columns))
report, bundles = train_candidates(
    frame,
    SPEC["target"],
    screening["included"],
    split,
    models=SPEC["models"],
    resource=resource,
    score_config=SPEC["score"],
)
print(json.dumps({{"champion": report["champion"], "metrics": report["champion_metrics"]}}, ensure_ascii=False, indent=2))
'''


def review_generated_code(source: str) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {
            "verdict": "block",
            "findings": [{"code": "CODE_SYNTAX_INVALID", "message": str(exc), "severity": "blocking"}],
        }
    for node in ast.walk(tree):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported = [node.module.split(".")[0]]
        for name in imported:
            if name in FORBIDDEN_IMPORTS:
                findings.append(
                    {"code": "DANGEROUS_IMPORT", "message": f"禁止生成代码导入 {name}", "severity": "blocking"}
                )
            elif name not in ALLOWED_IMPORTS:
                findings.append(
                    {"code": "UNAPPROVED_IMPORT", "message": f"生成代码导入了未批准模块 {name}", "severity": "blocking"}
                )
        if isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in FORBIDDEN_CALLS:
                findings.append(
                    {"code": "DANGEROUS_CALL", "message": f"禁止生成代码调用 {name}", "severity": "blocking"}
                )
    required = {
        "TRAIN_ONLY_SCREENING": "screen_features(train" in source,
        "OOT_NOT_SELECTION": "OOT 不参与选择" in source,
        "FIXED_RANDOM_STATE": "random_state" in source,
    }
    for code, passed in required.items():
        if not passed:
            findings.append({"code": code, "message": "可复现代码缺少必要治理约束", "severity": "blocking"})
    return {
        "verdict": "block" if any(item["severity"] == "blocking" for item in findings) else "pass",
        "findings": findings,
        "checks": required,
    }


def extract_generated_spec(source: str) -> dict[str, Any]:
    """Read the embedded immutable SPEC without executing generated code."""
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "SPEC" for target in node.targets):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == "json"
            and value.func.attr == "loads"
            and value.args
            and isinstance(value.args[0], ast.Constant)
            and isinstance(value.args[0].value, str)
        ):
            break
        parsed = json.loads(value.args[0].value)
        if isinstance(parsed, dict):
            return parsed
        break
    raise ValueError("GENERATED_CODE_SPEC_INVALID")


def write_reproducible_notebook(path: Path, source: str, metadata: dict[str, Any]) -> Path:
    try:
        import nbformat
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("NBFORMAT_DEPENDENCY_REQUIRED") from exc
    notebook = nbformat.v4.new_notebook(
        metadata={
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "risk_model_agent": metadata,
        }
    )
    notebook.cells = [
        nbformat.v4.new_markdown_cell(
            "# 风控建模可复现 Notebook\n\n此文件由主 Agent 生成，并已由独立 Reviewer 审核。生产执行证据来自本地确定性 Worker。"
        ),
        nbformat.v4.new_code_cell(source),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(notebook, path)
    return path
