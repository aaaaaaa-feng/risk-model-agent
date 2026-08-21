from __future__ import annotations

import json
import shutil
import textwrap
import zipfile
from pathlib import Path
from typing import Any

from app.core.security import sha256_file

from .modeling import ModelBundle
from .package_runtime import (
    PACKAGE_SCHEMA,
    SKOPS_POLICY_VERSION,
    inspect_skops_types,
)


def _save_skops(value: Any, path: Path) -> None:
    try:
        import skops.io as sio
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("SKOPS_DEPENDENCY_REQUIRED") from exc
    sio.dump(value, path)


def _native_estimator(bundle: ModelBundle) -> Any | None:
    estimator = bundle.estimator
    if hasattr(estimator, "named_steps"):
        return estimator.named_steps.get("model")
    calibrated = getattr(estimator, "calibrated_classifiers_", [])
    if calibrated:
        inner = getattr(calibrated[0], "estimator", None)
        if hasattr(inner, "named_steps"):
            return inner.named_steps.get("model")
    return None


def _save_model(bundle: ModelBundle, directory: Path) -> list[dict[str, str]]:
    native = _native_estimator(bundle)
    files: list[dict[str, str]] = []
    if bundle.algorithm == "xgboost" and native is not None and hasattr(native, "save_model"):
        target = directory / "model.json"
        native.save_model(target)
        files.append({"role": "native_model", "format": "xgboost-json", "name": target.name})
    elif bundle.algorithm == "lightgbm" and native is not None and hasattr(native, "booster_"):
        target = directory / "model.txt"
        native.booster_.save_model(str(target))
        files.append({"role": "native_model", "format": "lightgbm-text", "name": target.name})
    elif bundle.algorithm == "catboost" and native is not None and hasattr(native, "save_model"):
        target = directory / "model.cbm"
        native.save_model(str(target), format="cbm")
        files.append({"role": "native_model", "format": "catboost-cbm", "name": target.name})
    if bundle.algorithm != "scorecard":
        target = directory / "scoring_pipeline.skops"
        _save_skops(bundle.estimator, target)
        files.append({"role": "executable_pipeline", "format": "skops", "name": target.name})
    return files


def _scorecard_rules(bundle: ModelBundle) -> dict[str, Any]:
    estimator = bundle.estimator
    if not all(hasattr(estimator, name) for name in ("features_", "binning_", "model_")):
        raise ValueError("SCORECARD_ESTIMATOR_NOT_EXPORTABLE")
    coefficients = estimator.model_.coef_[0]
    return {
        "schema_version": "risk-scorecard-rules/v1",
        "model_name": bundle.name,
        "features": list(estimator.features_),
        "binning": estimator.binning_,
        "intercept": float(estimator.model_.intercept_[0]),
        "coefficients": [float(value) for value in coefficients],
        "bad_probability": "sigmoid(intercept + sum(coefficient * WOE))",
        "higher_score_is_lower_risk": True,
    }


def build_model_package(
    bundle: ModelBundle,
    contract: dict[str, Any],
    destination: Path,
    dependency_lock: list[str],
) -> tuple[Path, dict[str, Any]]:
    directory = destination.parent / f"{destination.stem}_contents"
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=False)
    formats = _save_model(bundle, directory)
    if bundle.algorithm == "scorecard":
        scorecard_path = directory / "scorecard.json"
        scorecard_path.write_text(
            json.dumps(_scorecard_rules(bundle), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        formats.append(
            {"role": "scorecard_rules", "format": "scorecard-json", "name": scorecard_path.name}
        )
    contract_path = directory / "field_contract.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    score_config = directory / "score_config.json"
    score_config.write_text(
        json.dumps(bundle.score_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lock_path = directory / "requirements.lock"
    lock_path.write_text("\n".join(sorted(dependency_lock)) + "\n", encoding="utf-8")
    scoring_script = directory / "score.py"
    scoring_script.write_text(
        textwrap.dedent(
            """
            from pathlib import Path
            from risk_model_agent_package_runtime import cli

            ROOT = Path(__file__).resolve().parent

            def score(input_path: str, output_path: str, sheet: str | None = None) -> int:
                arguments = ["--input", input_path, "--output", output_path]
                if sheet:
                    arguments.extend(["--sheet", sheet])
                return cli(ROOT, arguments)

            if __name__ == "__main__":
                raise SystemExit(cli(ROOT))
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    helper = directory / "risk_model_agent_package_runtime.py"
    source = Path(__file__).with_name("package_runtime.py")
    helper.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    executable = next(
        (directory / item["name"] for item in formats if item["role"] == "executable_pipeline"),
        None,
    )
    trusted_types = (
        inspect_skops_types(executable, bundle.algorithm) if executable is not None else []
    )
    hashes = {
        path.name: sha256_file(path)
        for path in directory.iterdir()
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "schema_version": PACKAGE_SCHEMA,
        "model_name": bundle.name,
        "algorithm": bundle.algorithm,
        "calibration": bundle.calibration,
        "formats": formats,
        "field_contract": contract_path.name,
        "score_config": score_config.name,
        "hashes": hashes,
        "skops_policy": SKOPS_POLICY_VERSION,
        "skops_trusted_types": trusted_types,
        "raw_data_included": False,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.iterdir()):
            if path.is_file():
                archive.write(path, arcname=path.name)
    manifest["package_sha256"] = sha256_file(destination)
    return destination, manifest
