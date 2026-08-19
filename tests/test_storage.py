import hashlib
import json

import pytest

from app.domain import DomainError
from app.services.storage import Storage


def test_save_dataset_hash_and_atomic_json_round_trip(tmp_path):
    storage = Storage(tmp_path / "instance")
    content = b"feature,target\n1,0\n2,1\n"

    metadata = storage.save_dataset("proj_project-1", "sample.csv", content)

    assert storage.dataset_path("proj_project-1").read_bytes() == content
    assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
    assert metadata["size_bytes"] == len(content)
    assert metadata["stored_path"] == str(storage.dataset_path("proj_project-1"))
    assert storage.read_json("proj_project-1", "dataset.json") == metadata
    assert list(storage.project_dir("proj_project-1").glob("*.tmp")) == []

    path = storage.write_json("proj_project-1", "plan.json", {"version": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1}
    assert storage.read_json("proj_project-1", "plan.json") == {"version": 1}


@pytest.mark.parametrize("project_id", ["../escape", "a/b", "a\\b", "", ".", "project-1"])
def test_project_id_rejects_path_traversal(tmp_path, project_id):
    storage = Storage(tmp_path / "instance")
    with pytest.raises(DomainError) as caught:
        storage.project_dir(project_id)
    assert caught.value.code == "INVALID_PROJECT_ID"


@pytest.mark.parametrize(
    "filename,expected_code",
    [
        ("../data.csv", "UNSAFE_FILENAME"),
        ("folder/data.csv", "UNSAFE_FILENAME"),
        ("folder\\data.csv", "UNSAFE_FILENAME"),
        ("model.pkl", "UNSUPPORTED_DATASET_TYPE"),
    ],
)
def test_dataset_filename_is_csv_basename_only(tmp_path, filename, expected_code):
    storage = Storage(tmp_path / "instance")
    with pytest.raises(DomainError) as caught:
        storage.save_dataset("proj_safe-project", filename, b"a,b\n1,2\n")
    assert caught.value.code == expected_code


def test_missing_dataset_and_unsafe_artifact_are_controlled_errors(tmp_path):
    storage = Storage(tmp_path / "instance")
    with pytest.raises(DomainError) as missing:
        storage.dataset_path("proj_project")
    assert missing.value.status_code == 404

    with pytest.raises(DomainError) as unsafe:
        storage.write_json("proj_project", "../plan.json", {})
    assert unsafe.value.code == "UNSAFE_ARTIFACT_NAME"
