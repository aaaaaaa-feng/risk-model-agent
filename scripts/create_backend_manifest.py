#!/usr/bin/env python3
"""为冻结后端目录生成可重现的完整性清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tomllib
from pathlib import Path
from typing import Any, TextIO

MANIFEST_FILENAME = "backend-manifest.json"
SCHEMA_VERSION = "risk-model-agent/backend-manifest/v1"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
HASH_CHUNK_SIZE = 1024 * 1024


class ManifestError(RuntimeError):
    """后端清单无法安全生成。"""


def _console_safe_text(value: str, stream: TextIO) -> str:
    """按实际控制台编码降级，避免 Windows 旧代码页使任务假失败。"""

    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        value.encode(encoding)
    except LookupError:
        encoding = "ascii"
    except UnicodeEncodeError:
        pass
    else:
        return value
    return value.encode(encoding, errors="backslashreplace").decode(encoding)


def _print_console(value: str, *, stream: TextIO) -> None:
    print(_console_safe_text(value, stream), file=stream)


class _ConsoleSafeArgumentParser(argparse.ArgumentParser):
    """让帮助与参数错误也经过相同的控制台编码边界。"""

    def _print_message(self, message: str, file: TextIO | None = None) -> None:
        if message:
            stream = file or sys.stderr
            stream.write(_console_safe_text(message, stream))


def _sha256_file(path: Path) -> str:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    after = path.stat()
    identity_before = (before.st_size, before.st_mtime_ns, before.st_ino)
    identity_after = (after.st_size, after.st_mtime_ns, after.st_ino)
    if identity_before != identity_after:
        raise ManifestError(f"扫描期间文件发生变化：{path}")
    return digest.hexdigest()


def _walk_regular_files(root: Path, current: Path) -> list[Path]:
    files: list[Path] = []
    try:
        entries = sorted(os.scandir(current), key=lambda item: item.name.casefold())
    except OSError as error:
        raise ManifestError(f"无法读取后端目录 {current}：{error}") from error

    for entry in entries:
        path = Path(entry.path)
        try:
            mode = entry.stat(follow_symlinks=False).st_mode
        except OSError as error:
            raise ManifestError(f"无法读取后端资源 {path}：{error}") from error
        if stat.S_ISLNK(mode):
            try:
                target = path.resolve(strict=True)
                target.relative_to(root)
                target_mode = target.stat().st_mode
            except (OSError, ValueError) as error:
                raise ManifestError(
                    f"后端文件链接越出资源目录或目标无效：{path.relative_to(root).as_posix()}"
                ) from error
            if not stat.S_ISREG(target_mode):
                raise ManifestError(
                    f"后端仅允许指向目录内常规文件的链接：{path.relative_to(root).as_posix()}"
                )
            files.append(path)
            continue
        if stat.S_ISDIR(mode):
            files.extend(_walk_regular_files(root, path))
            continue
        if not stat.S_ISREG(mode):
            raise ManifestError(f"后端资源必须是常规文件：{path.relative_to(root).as_posix()}")
        relative = path.relative_to(root).as_posix()
        if relative != MANIFEST_FILENAME:
            files.append(path)
    return files


def create_manifest(root: Path, application_version: str) -> dict[str, Any]:
    """扫描 ``root`` 并返回稳定排序的清单对象。"""

    if not VERSION_PATTERN.fullmatch(application_version):
        raise ManifestError(f"应用版本不是有效的 SemVer：{application_version!r}")
    if root.is_symlink():
        raise ManifestError(f"后端根目录不允许是符号链接：{root}")
    if not root.is_dir():
        raise ManifestError(f"后端打包目录不存在：{root}")

    resolved_root = root.resolve(strict=True)
    paths = _walk_regular_files(resolved_root, resolved_root)
    entries: list[dict[str, Any]] = []
    seen_casefolded: set[str] = set()
    for path in sorted(paths, key=lambda item: item.relative_to(resolved_root).as_posix()):
        relative = path.relative_to(resolved_root).as_posix()
        folded = relative.casefold()
        if folded in seen_casefolded:
            raise ManifestError(f"后端资源存在大小写冲突：{relative}")
        seen_casefolded.add(folded)
        entries.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )

    if not entries:
        raise ManifestError("后端打包目录不能是空目录")
    return {
        "schema_version": SCHEMA_VERSION,
        "application_version": application_version,
        "files": entries,
    }


def serialize_manifest(manifest: dict[str, Any]) -> bytes:
    """使用固定 JSON 排版，保证相同输入产生相同摘要。"""

    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def write_manifest(root: Path, application_version: str) -> Path:
    manifest = create_manifest(root, application_version)
    payload = serialize_manifest(manifest)
    destination = root.resolve(strict=True) / MANIFEST_FILENAME
    temporary = destination.with_name(f".{MANIFEST_FILENAME}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def _default_version(repository_root: Path) -> str:
    with (repository_root / "pyproject.toml").open("rb") as handle:
        payload = tomllib.load(handle)
    try:
        version = str(payload["project"]["version"])
    except (KeyError, TypeError) as error:
        raise ManifestError("pyproject.toml 缺少 project.version") from error
    return version


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parent.parent
    parser = _ConsoleSafeArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=repository_root / "dist" / "risk-model-agent",
        help="PyInstaller onedir 根目录",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="应用 SemVer；默认读取 pyproject.toml",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    repository_root = Path(__file__).resolve().parent.parent
    try:
        version = args.version or _default_version(repository_root)
        destination = write_manifest(args.root, version)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        payload = json.loads(destination.read_text(encoding="utf-8"))
    except (ManifestError, OSError, tomllib.TOMLDecodeError) as error:
        _print_console(f"生成后端完整性清单失败：{error}", stream=sys.stderr)
        return 2

    _print_console(
        f"已生成 {destination} | files={len(payload['files'])} | sha256={digest}",
        stream=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
