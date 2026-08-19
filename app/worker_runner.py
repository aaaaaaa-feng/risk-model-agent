"""Bounded subprocess entry point for training tasks.

The Web process never executes generated code. This module only accepts a
server-created JSON task, reads a validated local dataset reference, and runs
the deterministic Worker implementation. It is intentionally tiny so the
parent can terminate it on timeout without corrupting the Web process.
"""

from __future__ import annotations

import json
import math
import numbers
import sys
from pathlib import Path
from typing import Any, Dict

from .worker import read_table, train_candidates


def _json_safe(value: Any) -> Any:
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        try:
            if not math.isfinite(float(value)):
                return None
        except (TypeError, ValueError):
            pass
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes, bytearray)):
        try:
            return _json_safe(value.tolist())
        except (TypeError, ValueError):
            pass
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def execute(payload: Dict[str, Any]) -> Dict[str, Any]:
    frame = read_table(Path(payload["dataset_path"]), payload.get("sheet"))
    return train_candidates(
        frame,
        payload["target"],
        payload["features"],
        payload["split"],
        Path(payload["output_dir"]),
        payload.get("models"),
        payload.get("baseline_column"),
    )


def main() -> None:
    payload = json.loads(sys.stdin.read())
    result = execute(payload)
    sys.stdout.write(json.dumps(_json_safe(result), ensure_ascii=False, sort_keys=True, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
