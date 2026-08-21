"""Standalone-safe runtime copied into every model package.

This module intentionally has no imports from ``app``.  A packaged scorer can
therefore run in a clean Python environment containing only the locked runtime
dependencies.
"""

from __future__ import annotations

import argparse
import json
import math
import stat
import zipfile
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd


PACKAGE_SCHEMA = "risk-model-package/v2"
LEGACY_PACKAGE_SCHEMA = "risk-model-package/v1"
SKOPS_POLICY_VERSION = "risk-skops-types/v1"
MAX_PACKAGE_FILES = 64
MAX_PACKAGE_UNPACKED_BYTES = 4 * 1024**3
MAX_PACKAGE_COMPRESSION_RATIO = 250

COMMON_SKOPS_TYPES = {
    "numpy.dtype",
    "sklearn.calibration._CalibratedClassifier",
    "sklearn.calibration._SigmoidCalibration",
}
ALGORITHM_SKOPS_TYPES = {
    "dummy": set(),
    "regularized_logistic": set(),
    "random_forest": set(),
    "extra_trees": set(),
    "xgboost": {"xgboost.core.Booster", "xgboost.sklearn.XGBClassifier"},
    "lightgbm": {
        "collections.OrderedDict",
        "lightgbm.basic.Booster",
        "lightgbm.sklearn.LGBMClassifier",
    },
    "catboost": {"catboost.core.CatBoostClassifier"},
    # V2 scorecards are JSON rules and never load this type.  It remains in the
    # versioned policy solely for a checksum-verified V1 package created locally.
    "scorecard": {"app.workers.modeling.ScorecardEstimator"},
}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def approved_skops_types(algorithm: str) -> set[str]:
    if algorithm not in ALGORITHM_SKOPS_TYPES:
        raise ValueError("MODEL_PACKAGE_ALGORITHM_UNSUPPORTED")
    return COMMON_SKOPS_TYPES | ALGORITHM_SKOPS_TYPES[algorithm]


def inspect_skops_types(
    model_file: Path,
    algorithm: str,
    declared: Sequence[str] | None = None,
) -> list[str]:
    try:
        import skops.io as sio
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("SKOPS_DEPENDENCY_REQUIRED") from exc
    discovered = sorted(set(sio.get_untrusted_types(file=model_file)))
    unknown = sorted(set(discovered) - approved_skops_types(algorithm))
    if unknown:
        raise ValueError(f"MODEL_SKOPS_TYPE_NOT_APPROVED: {unknown}")
    if declared is not None and sorted(set(declared)) != discovered:
        raise ValueError("MODEL_SKOPS_TYPE_MANIFEST_MISMATCH")
    return discovered


def load_skops_model(
    model_file: Path,
    algorithm: str,
    declared: Sequence[str] | None = None,
) -> Any:
    try:
        import skops.io as sio
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("SKOPS_DEPENDENCY_REQUIRED") from exc
    trusted = inspect_skops_types(model_file, algorithm, declared)
    return sio.load(model_file, trusted=trusted)


def verify_package_directory(root: Path, *, allow_legacy: bool = False) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("MODEL_PACKAGE_MANIFEST_MISSING")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("MODEL_PACKAGE_MANIFEST_INVALID") from exc
    schema = manifest.get("schema_version")
    if schema != PACKAGE_SCHEMA and not (allow_legacy and schema == LEGACY_PACKAGE_SCHEMA):
        raise ValueError("MODEL_PACKAGE_SCHEMA_UNSUPPORTED")
    hashes = manifest.get("hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("MODEL_PACKAGE_HASH_MANIFEST_INVALID")
    actual_files = {
        path.name for path in root.iterdir() if path.is_file() and path.name != "manifest.json"
    }
    expected_files = set(hashes)
    if actual_files != expected_files:
        raise ValueError("MODEL_PACKAGE_FILE_SET_MISMATCH")
    for name, expected in hashes.items():
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("MODEL_PACKAGE_FILE_NAME_INVALID")
        path = root / name
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"MODEL_PACKAGE_FILE_HASH_MISMATCH: {name}")
    model_name = manifest.get("model_name")
    algorithm = manifest.get("algorithm")
    if not isinstance(model_name, str) or not model_name or algorithm not in ALGORITHM_SKOPS_TYPES:
        raise ValueError("MODEL_PACKAGE_IDENTITY_INVALID")
    for pointer in ("field_contract", "score_config"):
        _validated_manifest_file(manifest.get(pointer), expected_files, pointer)
    formats = manifest.get("formats")
    if not isinstance(formats, list):
        raise ValueError("MODEL_PACKAGE_FORMATS_INVALID")
    for item in formats:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("role"), str)
            or not item["role"]
            or not isinstance(item.get("format"), str)
            or not item["format"]
        ):
            raise ValueError("MODEL_PACKAGE_FORMATS_INVALID")
        _validated_manifest_file(item.get("name"), expected_files, "formats.name")
    if schema == PACKAGE_SCHEMA:
        if manifest.get("skops_policy") != SKOPS_POLICY_VERSION:
            raise ValueError("MODEL_PACKAGE_SKOPS_POLICY_UNSUPPORTED")
        algorithm = str(manifest.get("algorithm") or "")
        executable = _format_file(manifest, "executable_pipeline")
        if algorithm == "scorecard":
            if not (root / "scorecard.json").is_file() or executable is not None:
                raise ValueError("MODEL_PACKAGE_SCORECARD_FORMAT_INVALID")
        elif executable is None:
            raise ValueError("MODEL_PACKAGE_EXECUTABLE_MISSING")
        else:
            inspect_skops_types(
                root / executable,
                algorithm,
                manifest.get("skops_trusted_types") or [],
            )
    return manifest


def _validated_manifest_file(value: Any, expected_files: set[str], field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or Path(value).name != value
        or value not in expected_files
    ):
        raise ValueError(f"MODEL_PACKAGE_FILE_REFERENCE_INVALID: {field}")
    return value


def safe_extract_model_package(package: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(package) as archive:
        members = archive.infolist()
        if not members or len(members) > MAX_PACKAGE_FILES:
            raise ValueError("MODEL_PACKAGE_MEMBER_LIMIT_EXCEEDED")
        names = [member.filename for member in members]
        if len(names) != len(set(names)):
            raise ValueError("MODEL_PACKAGE_DUPLICATE_MEMBER")
        total = 0
        root = destination.resolve()
        for member in members:
            if member.flag_bits & 0x1:
                raise ValueError("MODEL_PACKAGE_ENCRYPTED_MEMBER_FORBIDDEN")
            mode = (member.external_attr >> 16) & 0xFFFF
            path = Path(member.filename)
            if (
                member.is_dir()
                or path.is_absolute()
                or len(path.parts) != 1
                or ".." in path.parts
                or "\x00" in member.filename
                or (mode and stat.S_ISLNK(mode))
            ):
                raise ValueError("MODEL_PACKAGE_MEMBER_INVALID")
            total += member.file_size
            if total > MAX_PACKAGE_UNPACKED_BYTES:
                raise ValueError("MODEL_PACKAGE_UNPACKED_SIZE_LIMIT_EXCEEDED")
            if member.file_size and member.compress_size == 0:
                raise ValueError("MODEL_PACKAGE_COMPRESSION_RATIO_INVALID")
            if (
                member.compress_size
                and member.file_size / member.compress_size > MAX_PACKAGE_COMPRESSION_RATIO
            ):
                raise ValueError("MODEL_PACKAGE_COMPRESSION_RATIO_INVALID")
            target = (destination / member.filename).resolve()
            if root not in target.parents:
                raise ValueError("MODEL_PACKAGE_PATH_TRAVERSAL")
            with archive.open(member) as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
    return destination


def validate_frame_contract(frame: pd.DataFrame, contract: dict[str, Any]) -> pd.DataFrame:
    required = contract.get("required_fields")
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise ValueError("MODEL_FIELD_CONTRACT_INVALID")
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"MISSING_REQUIRED_FIELDS: {missing}")
    result = frame.copy()
    field_types = dict(contract.get("field_types") or {})
    dtypes = dict(contract.get("dtypes") or {})
    for column in required:
        semantic = field_types.get(column)
        expected = str(dtypes.get(column) or "")
        if semantic is None:
            semantic = (
                "numeric"
                if expected.lower().startswith(("int", "uint", "float", "decimal"))
                else "categorical"
            )
        if semantic == "numeric":
            converted = pd.to_numeric(result[column], errors="coerce")
            invalid = result[column].notna() & converted.isna()
            if invalid.any():
                raise ValueError(f"FIELD_TYPE_MISMATCH: {column}: numeric")
            result[column] = converted
        elif semantic == "datetime":
            converted = pd.to_datetime(result[column], errors="coerce")
            invalid = result[column].notna() & converted.isna()
            if invalid.any():
                raise ValueError(f"FIELD_TYPE_MISMATCH: {column}: datetime")
            result[column] = converted
        elif semantic == "categorical":
            if (
                result[column]
                .map(
                    lambda value: (
                        value is None
                        or isinstance(value, (str, bool, int, float, np.integer, np.floating))
                    )
                )
                .all()
            ):
                # Training object/string categories are represented canonically as
                # strings.  Converting numeric-looking inputs avoids silently
                # treating every value as an unknown OneHot category.
                if expected.lower().startswith(("object", "string", "category")):
                    missing = result[column].isna()
                    result[column] = result[column].astype(str).where(~missing, np.nan)
                continue
            raise ValueError(f"FIELD_TYPE_MISMATCH: {column}: categorical")
        else:
            raise ValueError(f"MODEL_FIELD_TYPE_UNSUPPORTED: {column}")
    return result


def score_package_directory(
    root: Path, frame: pd.DataFrame, *, allow_legacy: bool = False
) -> tuple[np.ndarray, dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest = verify_package_directory(root, allow_legacy=allow_legacy)
    contract = _read_json(root / str(manifest.get("field_contract") or "field_contract.json"))
    config = _read_json(root / str(manifest.get("score_config") or "score_config.json"))
    validated = validate_frame_contract(frame, contract)
    required = contract["required_fields"]
    algorithm = str(manifest.get("algorithm") or "")
    if algorithm == "scorecard" and (root / "scorecard.json").is_file():
        probability = _score_scorecard(validated[required], _read_json(root / "scorecard.json"))
    else:
        executable = _format_file(manifest, "executable_pipeline") or "scoring_pipeline.skops"
        model = load_skops_model(
            root / executable,
            algorithm,
            manifest.get("skops_trusted_types")
            if manifest.get("schema_version") == PACKAGE_SCHEMA
            else None,
        )
        probability = np.asarray(model.predict_proba(validated[required])[:, 1], dtype=float)
    if len(probability) != len(frame) or not np.isfinite(probability).all():
        raise ValueError("MODEL_SCORE_OUTPUT_INVALID")
    return probability, contract, config, manifest


def append_scores(
    frame: pd.DataFrame,
    probability: np.ndarray,
    model_name: str,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    minimum = float(config.get("minimum", 300))
    maximum = float(config.get("maximum", 900))
    base_score = float(config.get("base_score", 600))
    base_odds = float(config.get("base_odds", 20))
    pdo = float(config.get("pdo", 50))
    if not (minimum < maximum and base_odds > 0 and pdo > 0):
        raise ValueError("SCORE_CONFIG_INVALID")
    clipped_probability = np.clip(np.asarray(probability, dtype=float), 1e-9, 1 - 1e-9)
    odds = (1 - clipped_probability) / clipped_probability
    raw_score = base_score + pdo / math.log(2) * np.log(odds / base_odds)
    score = np.clip(raw_score, minimum, maximum)
    base_name = "".join(char if char.isalnum() else "_" for char in model_name).strip("_")
    score_column = f"{base_name or 'risk_model'}_score"
    probability_column = f"{score_column}_bad_probability"
    output = frame.copy()
    output[score_column] = np.rint(score).astype(int)
    output[probability_column] = clipped_probability
    return output, {
        "score_column": score_column,
        "probability_column": probability_column,
        "floor_rate": float(np.mean(raw_score < minimum)),
        "cap_rate": float(np.mean(raw_score > maximum)),
        "rows": len(frame),
    }


def cli(root: Path | None = None, argv: Sequence[str] | None = None) -> int:
    package_root = (root or Path(__file__).resolve().parent).resolve()
    parser = argparse.ArgumentParser(description="Risk Model Agent 独立批量评分")
    parser.add_argument("--input", help="CSV/XLSX 输入文件")
    parser.add_argument("--output", help="CSV/XLSX 输出文件")
    parser.add_argument("--sheet", help="Excel Sheet 名")
    parser.add_argument("--verify-only", action="store_true", help="只校验模型包完整性")
    args = parser.parse_args(argv)
    verify_package_directory(package_root)
    if args.verify_only:
        print(json.dumps({"status": "ok", "verified": True}, ensure_ascii=False))
        return 0
    if not args.input or not args.output:
        parser.error("--input 与 --output 必填，或使用 --verify-only")
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    frame = _read_table(input_path, args.sheet)
    probability, contract, config, _ = score_package_directory(package_root, frame)
    scored, evidence = append_scores(frame, probability, contract["model_name"], config)
    _write_table(scored, output_path)
    print(json.dumps({"status": "ok", **evidence, "output": str(output_path)}, ensure_ascii=False))
    return 0


def _format_file(manifest: dict[str, Any], role: str) -> str | None:
    for item in manifest.get("formats") or []:
        if isinstance(item, dict) and item.get("role") == role:
            name = item.get("name")
            return str(name) if name else None
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"MODEL_PACKAGE_JSON_INVALID: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"MODEL_PACKAGE_JSON_INVALID: {path.name}")
    return value


def _apply_bin(series: pd.Series, specification: dict[str, Any]) -> pd.Series:
    if specification.get("kind") == "numeric":
        numeric = pd.to_numeric(series, errors="coerce")
        labels = pd.cut(
            numeric,
            [-np.inf, *specification.get("edges", []), np.inf],
            include_lowest=True,
        ).astype(str)
        return labels.where(numeric.notna(), "<MISSING>")
    values = series.astype("object").where(series.notna(), "<MISSING>").astype(str)
    rare = set(specification.get("rare_values") or [])
    values = values.map(lambda value: "<RARE>" if value in rare and value != "<MISSING>" else value)
    lookup = {
        str(value): f"G{index + 1:02d}"
        for index, group in enumerate(specification.get("groups") or [])
        for value in group
    }
    return values.map(lambda value: lookup.get(value, "<OTHER>"))


def _score_scorecard(frame: pd.DataFrame, rules: dict[str, Any]) -> np.ndarray:
    features = rules.get("features")
    coefficients = rules.get("coefficients")
    specifications = rules.get("binning", {}).get("specs")
    if (
        not isinstance(features, list)
        or not isinstance(coefficients, list)
        or len(features) != len(coefficients)
        or not isinstance(specifications, dict)
    ):
        raise ValueError("SCORECARD_RULES_INVALID")
    linear = np.full(len(frame), float(rules.get("intercept")), dtype=float)
    for column, coefficient in zip(features, coefficients, strict=True):
        specification = specifications.get(column)
        if not isinstance(specification, dict):
            raise ValueError(f"SCORECARD_RULES_INVALID: {column}")
        mapping = {
            str(row["bin"]): float(row["woe"])
            for row in specification.get("table") or []
            if isinstance(row, dict) and "bin" in row and "woe" in row
        }
        transformed = _apply_bin(frame[column], specification).map(mapping).fillna(0.0)
        linear += float(coefficient) * transformed.to_numpy(dtype=float)
    linear = np.clip(linear, -700, 700)
    return 1 / (1 + np.exp(-linear))


def _read_table(path: Path, sheet: str | None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin1"):
            try:
                return pd.read_csv(path, encoding=encoding)
            except UnicodeDecodeError:
                continue
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return pd.read_excel(path, sheet_name=sheet or 0)
    raise ValueError("UNSUPPORTED_TABLE_FORMAT")


def _write_table(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False, encoding="utf-8-sig")
    elif path.suffix.lower() == ".xlsx":
        frame.to_excel(path, index=False, engine="openpyxl")
    else:
        raise ValueError("UNSUPPORTED_OUTPUT_FORMAT")


if __name__ == "__main__":  # pragma: no cover - copied package entrypoint
    raise SystemExit(cli())
