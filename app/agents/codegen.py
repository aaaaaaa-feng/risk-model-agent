from __future__ import annotations

import ast
import json
import math
from pathlib import Path
from typing import Any, Sequence


GENERATED_CODE_POLICY = "risk-generated-code-template/v1"
ALLOWED_MODELS = {
    "dummy",
    "scorecard",
    "regularized_logistic",
    "random_forest",
    "extra_trees",
    "xgboost",
    "lightgbm",
    "catboost",
}


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
    """Authorize only the closed, deterministic modeling template.

    A denylist can always be bypassed through aliases, reflection, descriptors,
    or newly introduced APIs.  The product does not need arbitrary generated
    Python here: the Agent selects a typed specification and this function proves
    that the executable source is exactly the locally owned template for that
    specification.
    """
    try:
        specification = extract_generated_spec(source)
        _validate_generated_spec(specification)
        expected = generate_reproducible_notebook_code(
            dataset_file=specification["dataset_file"],
            target=specification["target"],
            features=specification["features"],
            split=specification["split"],
            models=specification["models"],
            score_config=specification["score"],
        )
    except (SyntaxError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return {
            "verdict": "block",
            "findings": [
                {
                    "code": str(exc).split(":", 1)[0] or "GENERATED_CODE_SPEC_INVALID",
                    "message": "生成代码没有通过封闭模板规范校验。",
                    "severity": "blocking",
                }
            ],
            "checks": {
                "policy": GENERATED_CODE_POLICY,
                "typed_spec": False,
                "canonical_template": False,
            },
        }
    canonical = _normalize_source(source) == _normalize_source(expected)
    findings = (
        []
        if canonical
        else [
            {
                "code": "NON_CANONICAL_GENERATED_CODE",
                "message": "代码含有封闭模板之外的语句、注释或改写，必须从已验证规范重新生成。",
                "severity": "blocking",
            }
        ]
    )
    return {
        "verdict": "pass" if canonical else "block",
        "findings": findings,
        "checks": {
            "policy": GENERATED_CODE_POLICY,
            "typed_spec": True,
            "canonical_template": canonical,
        },
    }


def _validate_generated_spec(specification: dict[str, Any]) -> None:
    expected_keys = {"dataset_file", "target", "features", "split", "models", "score"}
    if set(specification) != expected_keys:
        raise ValueError("GENERATED_CODE_SPEC_KEYS_INVALID")
    dataset_file = specification["dataset_file"]
    target = specification["target"]
    features = specification["features"]
    models = specification["models"]
    split = specification["split"]
    score = specification["score"]
    if not isinstance(dataset_file, str) or not dataset_file or len(dataset_file) > 4096:
        raise ValueError("GENERATED_CODE_DATASET_PATH_INVALID")
    if not isinstance(target, str) or not target or len(target) > 512:
        raise ValueError("GENERATED_CODE_TARGET_INVALID")
    if (
        not isinstance(features, list)
        or not features
        or any(not isinstance(value, str) or not value or len(value) > 512 for value in features)
    ):
        raise ValueError("GENERATED_CODE_FEATURES_INVALID")
    if len(features) != len(set(features)) or target in features:
        raise ValueError("GENERATED_CODE_FEATURES_INVALID")
    if (
        not isinstance(models, list)
        or not models
        or any(not isinstance(value, str) or value not in ALLOWED_MODELS for value in models)
    ):
        raise ValueError("GENERATED_CODE_MODELS_INVALID")
    if len(models) != len(set(models)):
        raise ValueError("GENERATED_CODE_MODELS_INVALID")
    if not isinstance(split, dict) or set(split) != {
        "method",
        "time_column",
        "customer_key",
        "random_state",
    }:
        raise ValueError("GENERATED_CODE_SPLIT_INVALID")
    if split["method"] not in {"time_holdout", "random_stratified"}:
        raise ValueError("GENERATED_CODE_SPLIT_INVALID")
    for key in ("time_column", "customer_key"):
        if split[key] is not None and not isinstance(split[key], str):
            raise ValueError("GENERATED_CODE_SPLIT_INVALID")
    if not isinstance(split["random_state"], int) or isinstance(split["random_state"], bool):
        raise ValueError("GENERATED_CODE_SPLIT_INVALID")
    if split["method"] == "time_holdout" and not split["time_column"]:
        raise ValueError("GENERATED_CODE_SPLIT_INVALID")
    if not isinstance(score, dict):
        raise ValueError("GENERATED_CODE_SCORE_INVALID")
    allowed_score = {"minimum", "maximum", "base_score", "base_odds", "pdo"}
    if not set(score).issubset(allowed_score):
        raise ValueError("GENERATED_CODE_SCORE_INVALID")
    for value in score.values():
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise ValueError("GENERATED_CODE_SCORE_INVALID")
    if '"""' in json.dumps(specification, ensure_ascii=False) or "\x00" in dataset_file:
        raise ValueError("GENERATED_CODE_SPEC_ENCODING_INVALID")


def _normalize_source(source: str) -> str:
    return source.replace("\r\n", "\n").rstrip() + "\n"


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
