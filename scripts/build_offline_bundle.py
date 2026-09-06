"""Resolve a hash-locked wheel cache offline for the current target interpreter.

Run on the target OS/Python. pip's dry-run verifies tags, Requires-Python and
complete dependency closure without installing or downloading anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname


def validate_lock(text: str) -> None:
    logical = re.sub(r"\\\r?\n", " ", text)
    requirements = []
    for line in logical.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        hashes = re.findall(r"--hash=sha256:([0-9a-fA-F]{64})(?=\s|$)", line)
        requirement = re.sub(r"\s+--hash=sha256:[0-9a-fA-F]{64}(?=\s|$)", "", line)
        if (
            not hashes
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_.,-]+\])?==[A-Za-z0-9][A-Za-z0-9.!+_-]*(?:\s*;\s*[^\r\n]+)?",
                requirement,
            )
            or "--" in requirement
            or " @ " in requirement
        ):
            raise ValueError("OFFLINE_LOCK_REQUIRES_EXACT_PINS_AND_SHA256")
        requirements.append(line)
    if not requirements:
        raise ValueError("OFFLINE_LOCK_EMPTY")


def build_bundle(wheel_dir: Path, lock_path: Path, destination: Path) -> dict:
    wheel_dir, lock_path, destination = (
        wheel_dir.resolve(),
        lock_path.resolve(),
        destination.resolve(),
    )
    if not wheel_dir.is_dir() or not lock_path.is_file():
        raise ValueError("OFFLINE_INPUT_MISSING")
    if destination.exists():
        raise ValueError("OFFLINE_OUTPUT_EXISTS")
    lock_text = lock_path.read_text(encoding="utf-8")
    validate_lock(lock_text)
    with tempfile.TemporaryDirectory(prefix="risk-offline-resolve-") as temporary:
        staging = Path(temporary)
        frozen_lock = staging / "requirements.lock"
        frozen_lock.write_text(lock_text, encoding="utf-8")
        report_path = staging / "resolved.json"
        command = [
            sys.executable,
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "install",
            "--dry-run",
            "--ignore-installed",
            "--no-index",
            "--no-cache-dir",
            "--require-hashes",
            "--only-binary=:all:",
            "--find-links",
            str(wheel_dir),
            "--report",
            str(report_path),
            "-r",
            str(frozen_lock),
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=120, check=False
        )
        if completed.returncode != 0 or not report_path.is_file():
            raise ValueError(
                "OFFLINE_RESOLUTION_FAILED: 精确版本、SHA-256、平台标签或依赖闭包不满足"
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        installs = report.get("install") or []
        if not installs:
            raise ValueError("OFFLINE_RESOLUTION_EMPTY")
        selected = []
        for item in installs:
            info = item["download_info"]
            url = urlsplit(info["url"])
            if url.scheme != "file" or url.netloc not in {"", "localhost"}:
                raise ValueError("OFFLINE_RESOLVER_NONLOCAL_SOURCE")
            source = Path(url2pathname(url.path))
            if (
                source.is_symlink()
                or source.resolve().parent != wheel_dir
                or source.suffix != ".whl"
            ):
                raise ValueError("OFFLINE_RESOLVER_SOURCE_OUTSIDE_CACHE")
            digest = info.get("archive_info", {}).get("hashes", {}).get("sha256")
            if not digest or _sha256(source) != digest:
                raise ValueError("OFFLINE_WHEEL_CHECKSUM_MISMATCH")
            selected.append((source, digest))
        destination.mkdir(parents=True, exist_ok=False)
        try:
            copied = []
            for source, digest in selected:
                target = destination / source.name
                shutil.copy2(source, target)
                if _sha256(target) != digest:
                    raise ValueError("OFFLINE_WHEEL_CHECKSUM_MISMATCH")
                copied.append(
                    {"name": target.name, "size_bytes": target.stat().st_size, "sha256": digest}
                )
            shutil.copy2(frozen_lock, destination / "requirements.lock")
            manifest = {
                "schema_version": "risk-agent-offline-bundle/v2",
                "source_lock_sha256": _sha256(frozen_lock),
                "target_environment": report["environment"],
                "wheels": copied,
                "dependency_closure_verified": True,
                "downloaded_by_script": False,
                "installed_by_script": False,
                "install_command": "python -m pip --isolated install --no-index --require-hashes --only-binary=:all: --find-links . -r requirements.lock",
            }
            (destination / "offline-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
            )
        except BaseException:
            shutil.rmtree(destination)
            raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="在目标 OS/Python 上构建并校验离线依赖包")
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path("requirements.lock"),
        help="每项精确 == 版本与 --hash=sha256 的完整依赖锁",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = build_bundle(args.wheel_dir, args.lock, args.output)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
