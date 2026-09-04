from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from app.core.database import Database, new_id, now_iso
from app.core.paths import AppPaths, get_paths
from app.core.security import sha256_file
from app.workers.io import estimate_table, read_table, safe_file_name, write_table
from app.workers.joining import JoinStep, execute_join, recommend_keys, validate_join
from app.workers.profiling import (
    parse_data_dictionary,
    profile_frame,
    target_candidate,
    target_summary,
)


class CatalogService:
    def __init__(self, database: Database | None = None, paths: AppPaths | None = None):
        self.paths = paths or get_paths()
        self.database = database or Database(paths=self.paths)

    def create_project(
        self,
        name: str,
        description: str = "",
        mode: str = "semi_trusted",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("PROJECT_NAME_REQUIRED")
        if mode not in {"semi_trusted", "fully_trusted"}:
            raise ValueError("PROJECT_MODE_INVALID")
        created = now_iso()
        project = self.database.insert(
            "projects",
            {
                "id": new_id("prj"),
                "name": cleaned[:120],
                "description": description.strip()[:2000],
                "status": "active",
                "mode": mode,
                "created_at": created,
                "updated_at": created,
                "metadata_json": metadata or {},
            },
        )
        directory = self.paths.project_dir(project["id"])
        for child in ("assets", "datasets", "runs", "scores", "trash"):
            (directory / child).mkdir(parents=True, exist_ok=True)
        self._write_project_manifest(project)
        self.ensure_conversation(project["id"])
        return project

    def list_projects(self, include_archived: bool = True) -> list[dict[str, Any]]:
        projects = self.database.list("projects", order_by="updated_at DESC", limit=2000)
        return [
            project
            for project in projects
            if project.get("trashed_at") is None
            and (include_archived or project.get("status") != "archived")
        ]

    def get_project(self, project_id: str) -> dict[str, Any]:
        project = self.database.get("projects", project_id)
        if not project or project.get("trashed_at"):
            raise KeyError(project_id)
        return project

    def update_project(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.get_project(project_id)
        allowed = {
            key: payload[key]
            for key in ("name", "description", "mode", "metadata_json")
            if key in payload
        }
        if "mode" in allowed and allowed["mode"] not in {"semi_trusted", "fully_trusted"}:
            raise ValueError("PROJECT_MODE_INVALID")
        allowed["updated_at"] = now_iso()
        updated = self.database.update("projects", project_id, allowed)
        self._write_project_manifest(updated)
        return updated

    def archive_project(self, project_id: str) -> dict[str, Any]:
        self.get_project(project_id)
        timestamp = now_iso()
        updated = self.database.update(
            "projects",
            project_id,
            {"status": "archived", "archived_at": timestamp, "updated_at": timestamp},
        )
        self._write_project_manifest(updated)
        return updated

    def restore_project(self, project_id: str) -> dict[str, Any]:
        project = self.database.get("projects", project_id)
        if not project:
            raise KeyError(project_id)
        timestamp = now_iso()
        updated = self.database.update(
            "projects",
            project_id,
            {"status": "active", "archived_at": None, "trashed_at": None, "updated_at": timestamp},
        )
        self._write_project_manifest(updated)
        return updated

    def trash_project(self, project_id: str) -> dict[str, Any]:
        self.get_project(project_id)
        timestamp = now_iso()
        updated = self.database.update(
            "projects",
            project_id,
            {"status": "trashed", "trashed_at": timestamp, "updated_at": timestamp},
        )
        self._write_project_manifest(updated)
        return updated

    def _write_project_manifest(self, project: dict[str, Any]) -> None:
        """Keep a small human-readable project boundary beside its files.

        SQLite remains the source of truth.  This manifest is deliberately
        limited to non-sensitive identity and storage metadata so a project
        folder can be recognized during backup, migration, or manual inspection
        without exposing rows, labels, or API credentials.
        """
        project_id = str(project["id"])
        directory = self.paths.project_dir(project_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "risk-agent-project/v1",
            "project_id": project_id,
            "name": str(project.get("name") or ""),
            "status": str(project.get("status") or "active"),
            "mode": str(project.get("mode") or "semi_trusted"),
            "created_at": project.get("created_at"),
            "updated_at": project.get("updated_at"),
            "storage": {
                "assets": "assets/",
                "datasets": "datasets/",
                "runs": "runs/",
                "scores": "scores/",
                "trash": "trash/",
            },
        }
        target = self.paths.project_manifest(project_id)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def register_asset(
        self,
        project_id: str,
        staged_path: Path,
        original_name: str,
        kind: str = "feature",
        sheet: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.get_project(project_id)
        if kind not in {"base", "feature", "dictionary", "score_input"}:
            raise ValueError("DATA_ASSET_KIND_INVALID")
        asset_id = new_id("asset")
        asset_dir = self.paths.project_dir(project_id) / "assets" / asset_id
        asset_dir.mkdir(parents=True, exist_ok=True)
        destination = asset_dir / safe_file_name(original_name)
        if staged_path.resolve() != destination.resolve():
            shutil.copy2(staged_path, destination)
        estimate = estimate_table(destination, sheet)
        status = "sheet_selection_required" if estimate.get("requires_sheet_selection") else "ready"
        asset = self.database.insert(
            "data_assets",
            {
                "id": asset_id,
                "project_id": project_id,
                "name": original_name[:240],
                "kind": kind,
                "format": destination.suffix.lower().lstrip("."),
                "stored_path": str(destination),
                "sheet": estimate.get("sheet"),
                "sha256": estimate["sha256"],
                "size_bytes": estimate["size_bytes"],
                "rows": estimate.get("rows"),
                "columns": estimate.get("columns"),
                "status": status,
                "metadata_json": {
                    **(metadata or {}),
                    "sheets": estimate.get("sheets", []),
                    "resource_plan": estimate.get("resource_plan"),
                },
                "created_at": now_iso(),
            },
        )
        self.database.update(
            "projects", project_id, {"status": "data_imported", "updated_at": now_iso()}
        )
        return asset

    def choose_asset_sheet(self, asset_id: str, sheet: str) -> dict[str, Any]:
        asset = self.require("data_assets", asset_id)
        estimate = estimate_table(Path(asset["stored_path"]), sheet)
        return self.database.update(
            "data_assets",
            asset_id,
            {
                "sheet": sheet,
                "rows": estimate["rows"],
                "columns": estimate["columns"],
                "status": "ready",
                "metadata_json": {
                    **asset.get("metadata", {}),
                    "sheets": estimate.get("sheets", []),
                    "resource_plan": estimate["resource_plan"],
                },
            },
        )

    def attach_dictionary(self, asset_id: str, dictionary_asset_id: str) -> dict[str, Any]:
        asset = self.require("data_assets", asset_id)
        dictionary_asset = self.require("data_assets", dictionary_asset_id)
        if asset["project_id"] != dictionary_asset["project_id"]:
            raise ValueError("CROSS_PROJECT_DICTIONARY_FORBIDDEN")
        dictionary_frame = read_table(
            Path(dictionary_asset["stored_path"]), dictionary_asset.get("sheet")
        )
        dictionary = parse_data_dictionary(dictionary_frame)
        metadata = dict(asset.get("metadata") or {})
        metadata["dictionary_asset_id"] = dictionary_asset_id
        metadata["dictionary"] = dictionary
        return self.database.update("data_assets", asset_id, {"metadata_json": metadata})

    def materialize_asset(self, asset_id: str, label: str | None = None) -> dict[str, Any]:
        asset = self.require("data_assets", asset_id)
        if asset["status"] != "ready":
            raise ValueError("DATA_ASSET_NOT_READY")
        frame = read_table(Path(asset["stored_path"]), asset.get("sheet"))
        dictionary = (asset.get("metadata") or {}).get("dictionary")
        return self.create_dataset_version(
            asset["project_id"],
            frame,
            label or f"{asset['name']} · 原始版本",
            parent_ids=[asset_id],
            lineage={
                "kind": "materialize_asset",
                "asset_id": asset_id,
                "source_sha256": asset["sha256"],
            },
            dictionary=dictionary,
        )

    def create_dataset_version(
        self,
        project_id: str,
        frame: pd.DataFrame,
        label: str,
        parent_ids: Sequence[str],
        lineage: dict[str, Any],
        dictionary: dict[str, Any] | None = None,
        freeze: bool = False,
    ) -> dict[str, Any]:
        self.get_project(project_id)
        identifier = new_id("dsv")
        destination = self.paths.project_dir(project_id) / "datasets" / f"{identifier}.csv"
        write_table(frame, destination)
        profile = profile_frame(frame, dictionary)
        return self.database.insert(
            "dataset_versions",
            {
                "id": identifier,
                "project_id": project_id,
                "label": label[:240],
                "stored_path": str(destination),
                "format": "csv",
                "sheet": None,
                "rows": len(frame),
                "columns": len(frame.columns),
                "parent_ids_json": list(parent_ids),
                "lineage_json": {**lineage, "output_sha256": sha256_file(destination)},
                "profile_json": profile,
                "is_frozen": freeze,
                "created_at": now_iso(),
            },
        )

    def create_dataset_from_file(
        self,
        project_id: str,
        path: Path,
        label: str,
        parent_ids: Sequence[str],
        lineage: dict[str, Any],
        sheet: str | None = None,
    ) -> dict[str, Any]:
        return self.create_dataset_version(
            project_id,
            read_table(path, sheet),
            label,
            parent_ids,
            lineage,
        )

    def dataset_frame(self, dataset_version_id: str) -> pd.DataFrame:
        dataset = self.require("dataset_versions", dataset_version_id)
        return read_table(Path(dataset["stored_path"]), dataset.get("sheet"))

    def recommend_join(self, left_asset_id: str, right_asset_id: str) -> dict[str, Any]:
        left = self.require("data_assets", left_asset_id)
        right = self.require("data_assets", right_asset_id)
        self._same_project(left, right)
        return recommend_keys(
            read_table(Path(left["stored_path"]), left.get("sheet")),
            read_table(Path(right["stored_path"]), right.get("sheet")),
        )

    def preview_join(
        self,
        left_asset_id: str,
        right_asset_id: str,
        left_keys: Sequence[str],
        right_keys: Sequence[str],
        target_columns: Sequence[str] = (),
        customer_key: str | None = None,
    ) -> dict[str, Any]:
        left = self.require("data_assets", left_asset_id)
        right = self.require("data_assets", right_asset_id)
        self._same_project(left, right)
        return validate_join(
            read_table(Path(left["stored_path"]), left.get("sheet")),
            read_table(Path(right["stored_path"]), right.get("sheet")),
            left_keys,
            right_keys,
            target_columns,
            customer_key,
        )

    def create_join_plan(
        self,
        project_id: str,
        name: str,
        base_asset_id: str,
        steps: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        base = self.require("data_assets", base_asset_id)
        if base["project_id"] != project_id:
            raise ValueError("CROSS_PROJECT_JOIN_FORBIDDEN")
        for step in steps:
            right = self.require("data_assets", str(step["right_asset_id"]))
            if right["project_id"] != project_id:
                raise ValueError("CROSS_PROJECT_JOIN_FORBIDDEN")
            JoinStep(**step)
        timestamp = now_iso()
        return self.database.insert(
            "join_plans",
            {
                "id": new_id("join"),
                "project_id": project_id,
                "name": name[:160],
                "status": "draft",
                "base_asset_id": base_asset_id,
                "steps_json": list(steps),
                "validation_json": {},
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )

    def execute_join_plan(
        self,
        plan_id: str,
        target_columns: Sequence[str] = (),
        customer_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        plan = self.require("join_plans", plan_id)
        base_asset = self.require("data_assets", plan["base_asset_id"])
        frame = read_table(Path(base_asset["stored_path"]), base_asset.get("sheet"))
        checked_targets = list(target_columns) or [
            str(column) for column in frame.columns if target_candidate(frame[column]) is not None
        ]
        inferred_customer_key = customer_key
        if not inferred_customer_key and plan["steps"]:
            first_keys = plan["steps"][0].get("left_keys") or []
            inferred_customer_key = str(first_keys[0]) if first_keys else None
        lineage_steps: list[dict[str, Any]] = []
        parents = [base_asset["id"]]
        dictionary_fields: dict[str, Any] = {}
        dictionary_mapping: dict[str, Any] = {}
        for name, value in (
            ((base_asset.get("metadata") or {}).get("dictionary") or {}).get("fields", {}).items()
        ):
            dictionary_fields[name] = value
        for raw in plan["steps"]:
            step = JoinStep(**raw)
            right = self.require("data_assets", step.right_asset_id)
            parents.append(right["id"])
            source_dictionary = (right.get("metadata") or {}).get("dictionary") or {}
            dictionary_fields.update(source_dictionary.get("fields") or {})
            dictionary_mapping.update(source_dictionary.get("mapping") or {})
            frame, evidence = execute_join(
                frame,
                read_table(Path(right["stored_path"]), right.get("sheet")),
                step,
                checked_targets,
                inferred_customer_key,
            )
            lineage_steps.append(evidence)
        target_checks = {
            target: {
                key: value
                for key, value in target_summary(frame, target).items()
                if key not in {"valid_mask", "normalized"}
            }
            for target in checked_targets
        }
        for result in target_checks.values():
            blocking = [item for item in result["issues"] if item["severity"] == "blocking"]
            if blocking:
                raise ValueError(blocking[0]["code"])
        dataset = self.create_dataset_version(
            plan["project_id"],
            frame,
            f"{plan['name']} · 关联结果",
            parents,
            {
                "kind": "join",
                "join_plan_id": plan_id,
                "steps": lineage_steps,
                "checked_targets": target_checks,
                "customer_key": inferred_customer_key,
            },
            dictionary={
                "fields": dictionary_fields,
                "mapping": dictionary_mapping,
                "rows": len(dictionary_fields),
            }
            if dictionary_fields
            else None,
        )
        updated = self.database.update(
            "join_plans",
            plan_id,
            {
                "status": "completed",
                "validation_json": {
                    "steps": lineage_steps,
                    "target_checks": target_checks,
                    "customer_key": inferred_customer_key,
                },
                "output_dataset_version_id": dataset["id"],
                "updated_at": now_iso(),
            },
        )
        return updated, dataset

    def create_target_task(
        self,
        project_id: str,
        dataset_version_id: str,
        target_column: str,
        labels: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        dataset = self.require("dataset_versions", dataset_version_id)
        if dataset["project_id"] != project_id:
            raise ValueError("CROSS_PROJECT_TARGET_FORBIDDEN")
        frame = self.dataset_frame(dataset_version_id)
        summary = target_summary(frame, target_column)
        blocking = [item for item in summary["issues"] if item["severity"] == "blocking"]
        if blocking:
            raise ValueError(blocking[0]["code"])
        existing = self.database.list("target_tasks", {"project_id": project_id}, limit=5000)
        timestamp = now_iso()
        return self.database.insert(
            "target_tasks",
            {
                "id": new_id("target"),
                "project_id": project_id,
                "dataset_version_id": dataset_version_id,
                "target_column": target_column,
                "status": "queued",
                "labels_json": labels or {"positive": 1, "negative": 0, "excluded": [-1, None]},
                "valid_sample_count": summary["valid_count"],
                "split_json": {},
                "screening_json": {},
                "binning_json": {},
                "model_plan_json": {},
                "queue_position": len(existing) + 1,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )

    def ensure_conversation(self, project_id: str) -> dict[str, Any]:
        existing = self.database.list("conversations", {"project_id": project_id}, limit=1)
        if existing:
            return existing[0]
        timestamp = now_iso()
        return self.database.insert(
            "conversations",
            {
                "id": new_id("conv"),
                "project_id": project_id,
                "run_id": None,
                "title": "项目 Agent 对话",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )

    def add_message(
        self,
        project_id: str,
        role: str,
        content: str,
        agent: str | None = None,
        summary: str = "",
    ) -> dict[str, Any]:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("CONVERSATION_ROLE_INVALID")
        conversation = self.ensure_conversation(project_id)
        message = self.database.insert(
            "conversation_messages",
            {
                "id": new_id("msg"),
                "conversation_id": conversation["id"],
                "role": role,
                "agent": agent,
                "content": content[:20000],
                "summary": summary[:1000],
                "created_at": now_iso(),
            },
        )
        self.database.update("conversations", conversation["id"], {"updated_at": now_iso()})
        return message

    def require(self, table: str, identifier: str) -> dict[str, Any]:
        value = self.database.get(table, identifier)
        if value is None:
            raise KeyError(identifier)
        return value

    @staticmethod
    def _same_project(left: dict[str, Any], right: dict[str, Any]) -> None:
        if left["project_id"] != right["project_id"]:
            raise ValueError("CROSS_PROJECT_OPERATION_FORBIDDEN")


def serialize_project_resources(database: Database, project_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "risk-project-export/v1",
        "project": database.get("projects", project_id),
    }
    for table in (
        "data_assets",
        "dataset_versions",
        "join_plans",
        "target_tasks",
        "runs",
        "conversations",
        "notebooks",
        "score_jobs",
    ):
        payload[table] = database.list_all(table, {"project_id": project_id})
    run_ids = [row["id"] for row in payload["runs"]]
    conversation_ids = [row["id"] for row in payload["conversations"]]
    for table in (
        "run_manifests",
        "traces",
        "trace_spans",
        "checkpoints",
        "review_records",
        "model_versions",
        "artifacts",
        "decisions",
        "events",
        "provider_requests",
    ):
        payload[table] = [
            item
            for run_id in run_ids
            for item in database.list_all(
                table,
                {"run_id": run_id},
                order_by=(
                    "started_at ASC" if table in {"traces", "trace_spans"} else "created_at ASC"
                ),
            )
        ]
    payload["conversation_messages"] = [
        item
        for conversation_id in conversation_ids
        for item in database.list_all(
            "conversation_messages", {"conversation_id": conversation_id}, order_by="created_at ASC"
        )
    ]
    payload["conversation_events"] = [
        item
        for conversation_id in conversation_ids
        for item in database.list_all(
            "conversation_events", {"conversation_id": conversation_id}, order_by="seq ASC"
        )
    ]
    message_ids = [item["id"] for item in payload["conversation_messages"]]
    payload["message_feedback"] = [
        item
        for message_id in message_ids
        for item in database.list_all(
            "message_feedback", {"message_id": message_id}, order_by="created_at ASC"
        )
    ]
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))
