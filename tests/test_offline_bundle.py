from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts.build_offline_bundle import build_bundle, validate_lock


def wheel(root: Path, name="audit_demo", version="1.0", dependency=None, tag="py3-none-any"):
    root.mkdir(exist_ok=True)
    path = root / f"{name}-{version}-{tag}.whl"
    dist = f"{name}-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}/__init__.py", "VALUE = 42\n")
        archive.writestr(
            f"{dist}/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            + (f"Requires-Dist: {dependency}\n" if dependency else ""),
        )
        archive.writestr(
            f"{dist}/WHEEL", f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n"
        )
        archive.writestr(f"{dist}/RECORD", "")
    return path


def pin(path: Path, name="audit_demo", version="1.0"):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"{name}=={version} --hash=sha256:{digest}\n"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "audit_demo>=1",
        "audit_demo==1",
        "--extra-index-url https://example.invalid",
        "audit_demo @ https://example.invalid/a.whl",
    ],
)
def test_lock_rejects_unpinned_or_unhashed_inputs(text):
    with pytest.raises(ValueError, match="OFFLINE_LOCK"):
        validate_lock(text)


@pytest.mark.parametrize("fault", ["version", "hash", "dependency", "platform"])
def test_offline_bundle_rejects_wrong_cache_without_publishing(tmp_path, fault):
    cache = tmp_path / "wheels"
    path = wheel(
        cache,
        version="2.0" if fault == "version" else "1.0",
        dependency="audit_missing==1.0" if fault == "dependency" else None,
        tag="cp27-cp27m-win32" if fault == "platform" else "py3-none-any",
    )
    lock = tmp_path / "requirements.lock"
    text = pin(path)
    if fault == "hash":
        text = "audit_demo==1.0 --hash=sha256:" + "0" * 64 + "\n"
    lock.write_text(text)
    output = tmp_path / "offline"
    with pytest.raises(ValueError, match="OFFLINE_RESOLUTION_FAILED"):
        build_bundle(cache, lock, output)
    assert not output.exists()


def test_verified_offline_bundle_installs_with_hashes_and_no_index(tmp_path):
    cache = tmp_path / "wheel cache 中文"
    dependency = wheel(cache, name="audit_dependency")
    main = wheel(cache, dependency="audit_dependency==1.0")
    wheel(cache, name="unused_demo")
    lock = tmp_path / "requirements.lock"
    lock.write_text(pin(main) + pin(dependency, "audit_dependency"))
    output = tmp_path / "offline"
    manifest = build_bundle(cache, lock, output)
    assert manifest["dependency_closure_verified"] is True
    assert {value["name"] for value in manifest["wheels"]} == {main.name, dependency.name}
    assert (output / "requirements.lock").read_text() == lock.read_text()
    target = tmp_path / "target"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--no-cache-dir",
            "--ignore-installed",
            "--require-hashes",
            "--only-binary=:all:",
            "--find-links",
            str(output),
            "-r",
            str(output / "requirements.lock"),
            "--target",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); import audit_demo, audit_dependency; print(audit_demo.VALUE + audit_dependency.VALUE)",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "84"
    assert (
        json.loads((output / "offline-manifest.json").read_text())["downloaded_by_script"] is False
    )
