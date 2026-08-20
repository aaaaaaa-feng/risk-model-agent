from __future__ import annotations

import json
import textwrap
import zipfile
from pathlib import Path
from typing import Any

from app.core.security import sha256_file

from .modeling import ModelBundle


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
    target = directory / "scoring_pipeline.skops"
    _save_skops(bundle.estimator, target)
    files.append({"role": "executable_pipeline", "format": "skops", "name": target.name})
    return files


def build_model_package(
    bundle: ModelBundle,
    contract: dict[str, Any],
    destination: Path,
    dependency_lock: list[str],
) -> tuple[Path, dict[str, Any]]:
    directory = destination.parent / f"{destination.stem}_contents"
    directory.mkdir(parents=True, exist_ok=True)
    formats = _save_model(bundle, directory)
    contract_path = directory / "field_contract.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    score_config = directory / "score_config.json"
    score_config.write_text(json.dumps(bundle.score_config, ensure_ascii=False, indent=2), encoding="utf-8")
    lock_path = directory / "requirements.lock"
    lock_path.write_text("\n".join(sorted(dependency_lock)) + "\n", encoding="utf-8")
    scoring_script = directory / "score.py"
    scoring_script.write_text(
        textwrap.dedent(
            """
            from pathlib import Path
            import json
            import pandas as pd
            import skops.io as sio
            from risk_model_agent_scoring import append_scores

            ROOT = Path(__file__).resolve().parent

            def score(input_path: str, output_path: str) -> None:
                model_file = ROOT / "scoring_pipeline.skops"
                trusted = sio.get_untrusted_types(file=model_file)
                model = sio.load(model_file, trusted=trusted)
                contract = json.loads((ROOT / "field_contract.json").read_text(encoding="utf-8"))
                config = json.loads((ROOT / "score_config.json").read_text(encoding="utf-8"))
                frame = pd.read_csv(input_path) if input_path.lower().endswith(".csv") else pd.read_excel(input_path)
                missing = sorted(set(contract["required_fields"]) - set(frame.columns))
                if missing:
                    raise ValueError(f"MISSING_REQUIRED_FIELDS: {missing}")
                probability = model.predict_proba(frame[contract["required_fields"]])[:, 1]
                output, _ = append_scores(frame, probability, contract["model_name"], config)
                output.to_csv(output_path, index=False, encoding="utf-8-sig")
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    helper = directory / "risk_model_agent_scoring.py"
    source = Path(__file__).with_name("scoring.py")
    helper.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    hashes = {
        path.name: sha256_file(path)
        for path in directory.iterdir()
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "schema_version": "risk-model-package/v1",
        "model_name": bundle.name,
        "algorithm": bundle.algorithm,
        "calibration": bundle.calibration,
        "formats": formats,
        "field_contract": contract_path.name,
        "score_config": score_config.name,
        "hashes": hashes,
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
