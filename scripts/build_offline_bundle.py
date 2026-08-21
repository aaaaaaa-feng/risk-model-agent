"""Build a reproducible offline dependency directory from a wheel cache.

The script never downloads packages.  It fails closed when the requested cache
does not contain the project lock requirements, so an operator cannot mistake
an incomplete bundle for an offline installer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 Risk Model Agent 离线依赖包")
    parser.add_argument("--wheel-dir", type=Path, required=True, help="预先准备好的 wheel 缓存目录")
    parser.add_argument("--lock", type=Path, default=Path("requirements.lock"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    wheel_dir = args.wheel_dir.resolve()
    lock_path = args.lock.resolve()
    if not wheel_dir.is_dir() or not lock_path.is_file():
        raise SystemExit("OFFLINE_INPUT_MISSING: wheel-dir 与 lock 必须存在")
    requirements = [
        line.strip()
        for line in lock_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    wheels = sorted(
        path for path in wheel_dir.glob("*.whl") if path.is_file() and not path.is_symlink()
    )
    if not wheels:
        raise SystemExit("OFFLINE_WHEEL_CACHE_EMPTY")
    available_distributions = {
        path.name.split("-", 1)[0].replace("_", "-").lower() for path in wheels
    }
    missing = []
    for requirement in requirements:
        name, separator, _ = requirement.partition("==")
        normalized = name.replace("_", "-").lower()
        if separator and normalized != "python" and normalized not in available_distributions:
            missing.append(name)
    if missing:
        raise SystemExit(f"OFFLINE_REQUIREMENTS_MISSING: {', '.join(sorted(missing))}")
    destination = args.output.resolve()
    if destination.exists():
        raise SystemExit("OFFLINE_OUTPUT_EXISTS")
    destination.mkdir(parents=True, exist_ok=False)
    copied: list[dict[str, object]] = []
    for wheel in wheels:
        target = destination / wheel.name
        shutil.copy2(wheel, target)
        copied.append(
            {"name": wheel.name, "size_bytes": target.stat().st_size, "sha256": _sha256(target)}
        )
    manifest = {
        "schema_version": "risk-agent-offline-bundle/v1",
        "source_lock_sha256": _sha256(lock_path),
        "requirements": requirements,
        "wheels": copied,
        "downloaded_by_script": False,
        "install_command": "python -m pip install --no-index --find-links . *.whl",
    }
    (destination / "offline-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
