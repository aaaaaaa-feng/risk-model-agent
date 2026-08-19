"""Local, project-scoped storage with traversal protection and atomic writes."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Union

from app.domain import DomainError

PathLike = Union[str, os.PathLike]
_PROJECT_ID_RE = re.compile(r"^proj_[A-Za-z0-9_-]{1,59}$")
_SAFE_ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class Storage:
    """Owns all files written beneath an application instance directory.

    Project identifiers and artifact names are intentionally conservative.
    Callers never get an API that accepts an arbitrary absolute output path.
    """

    def __init__(self, instance_dir: PathLike) -> None:
        self.instance_dir = Path(instance_dir).expanduser().resolve()
        self.projects_dir = (self.instance_dir / "projects").resolve()
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str, create: bool = True) -> Path:
        project_id = self._validate_project_id(project_id)
        path = (self.projects_dir / project_id).resolve()
        self._assert_within(self.projects_dir, path)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def save_dataset(
        self,
        project_id: str,
        original_filename: str,
        content: bytes,
    ) -> Dict[str, Any]:
        """Atomically save one CSV dataset and return its immutable identity."""

        safe_name = self._validate_upload_name(original_filename)
        if not isinstance(content, (bytes, bytearray)):
            raise DomainError(
                400,
                "INVALID_DATASET_CONTENT",
                "Dataset content must be bytes.",
            )
        payload = bytes(content)
        if not payload:
            raise DomainError(400, "EMPTY_DATASET", "The uploaded CSV is empty.")

        project_dir = self.project_dir(project_id)
        dataset_path = (project_dir / "dataset.csv").resolve()
        self._assert_within(project_dir, dataset_path)
        self._atomic_write_bytes(dataset_path, payload)

        metadata: Dict[str, Any] = {
            "project_id": project_id,
            "original_filename": safe_name,
            "stored_filename": dataset_path.name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "path": str(dataset_path),
            "stored_path": str(dataset_path),
        }
        self.write_json(project_id, "dataset.json", metadata)
        return metadata

    def dataset_path(self, project_id: str) -> Path:
        project_dir = self.project_dir(project_id, create=False)
        path = (project_dir / "dataset.csv").resolve()
        self._assert_within(project_dir, path)
        if not path.is_file():
            raise DomainError(
                404,
                "DATASET_NOT_FOUND",
                "No dataset has been uploaded for this project.",
                {"project_id": project_id},
            )
        return path

    def write_json(self, project_id: str, filename: str, payload: Any) -> Path:
        """Serialize JSON to a same-directory temp file and atomically replace."""

        project_dir = self.project_dir(project_id)
        filename = self._validate_artifact_name(filename, required_suffix=".json")
        path = (project_dir / filename).resolve()
        self._assert_within(project_dir, path)
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise DomainError(
                422,
                "JSON_SERIALIZATION_FAILED",
                "The artifact cannot be represented as strict JSON.",
                {"filename": filename, "reason": str(exc)},
            ) from exc
        self._atomic_write_bytes(path, encoded)
        return path

    def read_json(self, project_id: str, filename: str) -> Any:
        project_dir = self.project_dir(project_id, create=False)
        filename = self._validate_artifact_name(filename, required_suffix=".json")
        path = (project_dir / filename).resolve()
        self._assert_within(project_dir, path)
        if not path.is_file():
            raise DomainError(
                404,
                "ARTIFACT_NOT_FOUND",
                "The requested JSON artifact does not exist.",
                {"project_id": project_id, "filename": filename},
            )
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise DomainError(
                500,
                "ARTIFACT_READ_FAILED",
                "The JSON artifact could not be read safely.",
                {"filename": filename},
            ) from exc

    @staticmethod
    def _validate_project_id(project_id: str) -> str:
        if not isinstance(project_id, str) or not _PROJECT_ID_RE.fullmatch(project_id):
            raise DomainError(
                400,
                "INVALID_PROJECT_ID",
                (
                    "Project ID must start with 'proj_' and contain only letters, "
                    "numbers, '-' and '_'."
                ),
            )
        return project_id

    @staticmethod
    def _validate_upload_name(filename: str) -> str:
        if not isinstance(filename, str) or not filename.strip():
            raise DomainError(400, "INVALID_FILENAME", "A filename is required.")
        filename = filename.strip()
        # Treat both POSIX and Windows separators as path components.
        if (
            "/" in filename
            or "\\" in filename
            or filename in {".", ".."}
            or Path(filename).is_absolute()
        ):
            raise DomainError(
                400,
                "UNSAFE_FILENAME",
                "Dataset filename must not contain a path.",
            )
        if Path(filename).suffix.lower() != ".csv":
            raise DomainError(
                415,
                "UNSUPPORTED_DATASET_TYPE",
                "Only CSV datasets are accepted in V1.",
                {"filename": filename},
            )
        return filename

    @staticmethod
    def _validate_artifact_name(filename: str, required_suffix: str) -> str:
        if not isinstance(filename, str) or not _SAFE_ARTIFACT_RE.fullmatch(filename):
            raise DomainError(
                400,
                "UNSAFE_ARTIFACT_NAME",
                "Artifact name contains unsafe characters or path components.",
            )
        if Path(filename).suffix.lower() != required_suffix:
            raise DomainError(
                400,
                "INVALID_ARTIFACT_TYPE",
                "Artifact filename has an invalid extension.",
                {"required_suffix": required_suffix},
            )
        return filename

    @staticmethod
    def _assert_within(root: Path, candidate: Path) -> None:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise DomainError(
                400,
                "PATH_OUTSIDE_PROJECT",
                "Resolved path is outside the permitted project directory.",
            ) from exc

    @staticmethod
    def _atomic_write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=str(path.parent),
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temp_path), str(path))
        except OSError as exc:
            raise DomainError(
                500,
                "ATOMIC_WRITE_FAILED",
                "The local artifact could not be saved atomically.",
                {"filename": path.name},
            ) from exc
        finally:
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
