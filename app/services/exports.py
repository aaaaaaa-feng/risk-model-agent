"""Copy verified, registered outputs to a user-selected local directory."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.core.security import sha256_file
from app.workers.io import safe_file_name


def export_file(source: Path, name: str, checksum: str, directory: str) -> dict[str, Any]:
    destination = Path(directory.strip()).expanduser()
    if not destination.is_absolute() or not destination.is_dir():
        raise ValueError("EXPORT_DIRECTORY_INVALID")
    destination = destination.resolve()
    if not source.is_file() or sha256_file(source) != checksum:
        raise ValueError("ARTIFACT_CHECKSUM_MISMATCH")
    filename = Path(safe_file_name(name))
    # Exclusive creation protects existing exports, links and concurrent saves.
    for number in range(1000):
        suffix = f" ({number})" if number else ""
        output = destination / f"{filename.stem}{suffix}{filename.suffix}"
        try:
            stream = output.open("xb")
        except FileExistsError:
            continue
        except OSError as exc:
            raise ValueError("EXPORT_WRITE_FAILED") from exc
        try:
            with stream, source.open("rb") as original:
                shutil.copyfileobj(original, stream, length=1024 * 1024)
            if sha256_file(output) != checksum:
                raise ValueError("ARTIFACT_CHECKSUM_MISMATCH")
            return {
                "path": str(output),
                "filename": output.name,
                "size_bytes": output.stat().st_size,
                "sha256": checksum,
            }
        except (OSError, ValueError) as exc:
            output.unlink(missing_ok=True)
            if isinstance(exc, ValueError):
                raise
            raise ValueError("EXPORT_WRITE_FAILED") from exc
    raise ValueError("EXPORT_TOO_MANY_COPIES")
