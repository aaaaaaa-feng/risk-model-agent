"""冻结包离线能力自检，不读写用户工作区。"""

from __future__ import annotations

import json
import sys
import time
from typing import Any
import warnings

import numpy as np
import pandas as pd

from app.workers.model_adapters import MODEL_REGISTRY


REQUIRED_MODELS = {
    "dummy",
    "scorecard",
    "regularized_logistic",
    "random_forest",
    "extra_trees",
    "xgboost",
    "lightgbm",
    "catboost",
}
SELF_TEST_SEED = 42

# 只减少自检训练量，不改变正式建模默认参数。
SELF_TEST_PARAMETERS: dict[str, dict[str, Any]] = {
    "scorecard": {"max_iter": 50},
    "random_forest": {
        "model__n_estimators": 4,
        "model__max_depth": 3,
        "model__min_samples_leaf": 1,
    },
    "extra_trees": {
        "model__n_estimators": 4,
        "model__max_depth": 3,
        "model__min_samples_leaf": 1,
    },
    "xgboost": {"model__n_estimators": 3, "model__max_depth": 2},
    "lightgbm": {
        "model__n_estimators": 3,
        "model__num_leaves": 4,
        "model__max_depth": 2,
    },
    "catboost": {"model__iterations": 3, "model__depth": 2},
}


def _fixed_sample() -> tuple[pd.DataFrame, pd.Series]:
    index = np.arange(48)
    frame = pd.DataFrame(
        {
            "age": 20 + index % 17,
            "income": 2500 + index * 37 + (index % 4) * 100,
            "tenure": index % 9,
        }
    )
    target = pd.Series(((index * 7 + index // 3) % 11 >= 6).astype(int), name="Y")
    return frame, target


def _model_checks() -> list[dict[str, Any]]:
    identifiers = set(MODEL_REGISTRY.identifiers)
    missing = sorted(REQUIRED_MODELS - identifiers)
    if missing:
        return [
            {
                "id": identifier,
                "status": "failed",
                "error": "模型未注册",
            }
            for identifier in missing
        ]
    frame, target = _fixed_sample()
    features = list(frame.columns)
    positives = int(target.sum())
    negatives = int(len(target) - positives)
    results: list[dict[str, Any]] = []
    for identifier in MODEL_REGISTRY.identifiers:
        started = time.monotonic()
        try:
            estimator = MODEL_REGISTRY.build(
                identifier,
                frame,
                features,
                positives,
                negatives,
            )
            parameters = SELF_TEST_PARAMETERS.get(identifier)
            if parameters:
                estimator.set_params(**parameters)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                estimator.fit(frame, target)
                probability = np.asarray(estimator.predict_proba(frame), dtype=float)
            if probability.shape != (len(frame), 2):
                raise RuntimeError("MODEL_PROBABILITY_SHAPE_INVALID")
            if not np.isfinite(probability).all():
                raise RuntimeError("MODEL_PROBABILITY_NOT_FINITE")
            if np.any(probability < 0) or np.any(probability > 1):
                raise RuntimeError("MODEL_PROBABILITY_OUT_OF_RANGE")
            results.append(
                {
                    "id": identifier,
                    "status": "passed",
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "id": identifier,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                }
            )
    return results


def _data_runtime_check() -> dict[str, Any]:
    started = time.monotonic()
    try:
        import duckdb

        with duckdb.connect(":memory:") as connection:
            answer = connection.execute("SELECT 6 * 7").fetchone()[0]
        if answer != 42:
            raise RuntimeError("DUCKDB_RESULT_INVALID")
        return {
            "status": "passed",
            "engine": "duckdb",
            "dependencies": {"pandas": True, "numpy": True, "duckdb": True},
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }


def _serialization_check() -> dict[str, Any]:
    started = time.monotonic()
    try:
        import skops.io as sio
        from sklearn.linear_model import LogisticRegression

        frame, target = _fixed_sample()
        estimator = LogisticRegression(
            max_iter=100,
            random_state=42,
            solver="liblinear",
        ).fit(frame, target)
        payload = sio.dumps(estimator)
        # 数据与模型均由本函数固定生成，不接收外部包；这里只验证冻结后的协议往返。
        trusted = sio.get_untrusted_types(data=payload)
        restored = sio.loads(payload, trusted=trusted)
        probability = np.asarray(restored.predict_proba(frame), dtype=float)
        if probability.shape != (len(frame), 2) or not np.isfinite(probability).all():
            raise RuntimeError("SKOPS_ROUNDTRIP_INVALID")
        return {
            "status": "passed",
            "format": "skops",
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }


def run_package_self_test() -> dict[str, Any]:
    """在内存中验证完整离线包的模型与数据引擎契约。"""

    started = time.monotonic()
    models = _model_checks()
    data_runtime = _data_runtime_check()
    serialization = _serialization_check()
    passed = bool(models) and all(item["status"] == "passed" for item in models)
    passed = passed and data_runtime["status"] == "passed" and serialization["status"] == "passed"
    return {
        "schema_version": "risk-package-self-test/v2",
        "status": "passed" if passed else "failed",
        "message": "冻结包能力自检通过" if passed else "冻结包能力自检失败",
        "models": models,
        "data_runtime": data_runtime,
        "serialization": serialization,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "random_seed": SELF_TEST_SEED,
        "network_used": False,
        "user_workspace_written": False,
    }


def main() -> int:
    report = run_package_self_test()
    stream = sys.stdout if report["status"] == "passed" else sys.stderr
    # 冻结程序可能运行在 Windows CP1252 控制台；ASCII 转义后仍是完整可解析的 JSON。
    print(json.dumps(report, ensure_ascii=True, sort_keys=True), file=stream)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
