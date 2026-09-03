from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _load_manifest_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "create_backend_manifest.py"
    spec = importlib.util.spec_from_file_location("create_backend_manifest", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifest_module = _load_manifest_module()


def test_manifest_is_deterministic_and_excludes_itself(tmp_path: Path) -> None:
    bundle = tmp_path / "risk-model-agent"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "risk-model-agent.exe").write_bytes(b"binary")
    (bundle / "_internal" / "settings.json").write_text('{"mode":"local"}', encoding="utf-8")
    (bundle / manifest_module.MANIFEST_FILENAME).write_text("old", encoding="utf-8")

    destination = manifest_module.write_manifest(bundle, "1.2.0")
    first = destination.read_bytes()
    manifest = json.loads(first)
    entries = manifest["files"]

    assert manifest["schema_version"] == "risk-model-agent/backend-manifest/v1"
    assert manifest["application_version"] == "1.2.0"
    assert [entry["path"] for entry in entries] == [
        "_internal/settings.json",
        "risk-model-agent.exe",
    ]
    assert all(entry["path"] != manifest_module.MANIFEST_FILENAME for entry in entries)
    assert entries[1]["size"] == len(b"binary")
    assert entries[1]["sha256"] == hashlib.sha256(b"binary").hexdigest()

    manifest_module.write_manifest(bundle, "1.2.0")
    assert destination.read_bytes() == first


def test_manifest_changes_when_a_file_changes(tmp_path: Path) -> None:
    bundle = tmp_path / "risk-model-agent"
    bundle.mkdir()
    binary = bundle / "risk-model-agent.exe"
    binary.write_bytes(b"first")
    first = manifest_module.serialize_manifest(manifest_module.create_manifest(bundle, "1.2.0"))

    binary.write_bytes(b"second")
    second = manifest_module.serialize_manifest(manifest_module.create_manifest(bundle, "1.2.0"))
    assert first != second


def test_manifest_accepts_internal_file_symlinks(tmp_path: Path) -> None:
    bundle = tmp_path / "risk-model-agent"
    bundle.mkdir()
    target = bundle / "target.bin"
    target.write_bytes(b"content")
    link = bundle / "linked.bin"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("当前平台不允许创建符号链接")

    manifest = manifest_module.create_manifest(bundle, "1.2.0")
    linked = next(entry for entry in manifest["files"] if entry["path"] == "linked.bin")
    assert linked["size"] == len(b"content")
    assert linked["sha256"] == hashlib.sha256(b"content").hexdigest()


def test_manifest_rejects_external_symlinks(tmp_path: Path) -> None:
    bundle = tmp_path / "risk-model-agent"
    bundle.mkdir()
    external = tmp_path / "outside.bin"
    external.write_bytes(b"secret")
    link = bundle / "linked.bin"
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip("当前平台不允许创建符号链接")

    with pytest.raises(manifest_module.ManifestError, match="越出资源目录"):
        manifest_module.create_manifest(bundle, "1.2.0")
