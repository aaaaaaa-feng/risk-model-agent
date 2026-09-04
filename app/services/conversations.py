from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from app.agents.evidence import build_safe_evidence
from app.agents.prompts import CONVERSATION_PROMPT
from app.core.config import SettingsStore
from app.core.database import Database, new_id, now_iso
from app.core.errors import normalize_error_code, public_error_message
from app.core.paths import AppPaths, get_paths
from app.core.security import validate_provider_text
from app.providers.gateway import ProviderGateway

from .catalog import CatalogService


@dataclass(frozen=True)
class ConversationScopeSnapshot:
    public: dict[str, Any]
    run: dict[str, Any] | None
    decision: dict[str, Any] | None


class ConversationService:
    def __init__(
        self,
        database: Database | None = None,
        paths: AppPaths | None = None,
        catalog: CatalogService | None = None,
    ):
        self.paths = paths or get_paths()
        self.database = database or Database(paths=self.paths)
        self.catalog = catalog or CatalogService(self.database, self.paths)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="risk-agent-chat")
        self._lock = threading.RLock()

    def send(
        self,
        project_id: str,
        content: str,
        context: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        cleaned = content.strip()
        if not cleaned:
            raise ValueError("MESSAGE_REQUIRED")
        self.catalog.get_project(project_id)
        snapshot = self._resolve_scope(project_id, context or {})
        conversation = self.catalog.ensure_conversation(project_id)
        history = self._history_snapshot(conversation["id"])
        user_message = self.catalog.add_message(project_id, "user", cleaned)
        response_id = new_id("response")
        self._append_event(
            conversation["id"],
            "queued",
            "main_agent",
            "",
            "已收到问题，正在读取当前项目状态",
            {
                "response_id": response_id,
                "user_message_id": user_message["id"],
                "context": snapshot.public,
            },
        )
        self._executor.submit(
            self._answer,
            project_id,
            conversation["id"],
            response_id,
            cleaned,
            user_message["id"],
            snapshot,
            history,
        )
        return {
            "user_message": user_message,
            "conversation_id": conversation["id"],
            "response_id": response_id,
            "context": snapshot.public,
        }

    def _answer(
        self,
        project_id: str,
        conversation_id: str,
        response_id: str,
        question: str,
        user_message_id: str,
        snapshot: ConversationScopeSnapshot,
        history_snapshot: list[dict[str, Any]],
    ) -> None:
        self._append_event(
            conversation_id,
            "analyzing",
            "main_agent",
            "",
            "正在结合当前 Run、Reviewer 与待确认节点生成答复",
            {
                "response_id": response_id,
                "user_message_id": user_message_id,
                "context": snapshot.public,
                "hidden_chain_of_thought_included": False,
            },
        )
        scope = snapshot.public
        run = snapshot.run
        decision = snapshot.decision
        state = (run or {}).get("state") or {}
        profile = state.get("profile") or {}
        target = state.get("target_evidence") or {}
        screening = state.get("screening") or {}
        model_result = state.get("model_result") or {}
        safe, aliases = build_safe_evidence(profile, target, screening, model_result)
        safe_question = question
        for original, alias in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
            safe_question = safe_question.replace(original, alias)
        provider_block: str | None = None
        try:
            validate_provider_text(safe_question)
        except ValueError as exc:
            provider_block = normalize_error_code(exc, "DLP_BLOCK")
        settings = SettingsStore(self.paths).load()
        gateway = ProviderGateway(settings=settings, paths=self.paths)
        answer = ""
        response_source = "local_fallback"
        if gateway.enabled and not provider_block:
            history = self._safe_history(history_snapshot, aliases)
            result = gateway.complete(
                CONVERSATION_PROMPT.content,
                {
                    "project_state": safe,
                    "current_run": {
                        "selected_explicitly": scope.get("selection") == "explicit",
                        "status": (run or {}).get("status"),
                        "stage": (run or {}).get("stage"),
                        "node": (run or {}).get("node"),
                        "progress": (run or {}).get("progress"),
                    },
                    "current_decision": {
                        "kind": (decision or {}).get("kind"),
                        "stage": (decision or {}).get("stage"),
                        "status": (decision or {}).get("status"),
                        "is_current": bool(scope.get("decision_is_current")),
                    },
                    "context_stage_changed": bool(scope.get("stage_changed")),
                    "conversation_history": history,
                    "question": safe_question,
                },
                purpose="project_conversation",
            )
            if result.ok:
                answer = result.content
                response_source = "provider"
            else:
                provider_block = normalize_error_code(
                    result.error_code,
                    "PROVIDER_REQUEST_FAILED",
                )
        elif not provider_block:
            provider_block = _provider_unavailable_reason(settings, gateway)
        if not answer:
            answer = self._fallback_answer(run, state, provider_block)
        message = self.catalog.add_message(
            project_id,
            "assistant",
            answer,
            agent="main_agent",
            summary=(
                "外部 LLM 基于当前项目状态生成的 Agent 答复"
                if response_source == "provider"
                else "API 未连接或不可用时生成的本地降级答复"
            ),
        )
        for chunk in _chunks(answer, 36):
            self._append_event(
                conversation_id,
                "delta",
                "main_agent",
                chunk,
                "",
                {
                    "response_id": response_id,
                    "message_id": message["id"],
                    "user_message_id": user_message_id,
                },
            )
        self._append_event(
            conversation_id,
            "completed",
            "main_agent",
            "",
            "答复完成",
            {
                "response_id": response_id,
                "message_id": message["id"],
                "provider_block": provider_block,
                "response_source": response_source,
                "user_message_id": user_message_id,
                "context": scope,
            },
        )

    def _resolve_scope(
        self,
        project_id: str,
        requested: dict[str, str | None],
    ) -> ConversationScopeSnapshot:
        requested_run_id = requested.get("run_id")
        requested_decision_id = requested.get("decision_id")
        requested_stage = requested.get("stage")
        run: dict[str, Any] | None = None
        decision: dict[str, Any] | None = None

        if requested_decision_id:
            decision = self.catalog.require("decisions", requested_decision_id)
            if requested_run_id and decision["run_id"] != requested_run_id:
                raise ValueError("CONVERSATION_DECISION_RUN_MISMATCH")
            requested_run_id = str(decision["run_id"])

        if requested_run_id:
            run = self.catalog.require("runs", requested_run_id)
            if run["project_id"] != project_id:
                raise ValueError("CONVERSATION_RUN_PROJECT_MISMATCH")
        else:
            runs = self.database.list(
                "runs", {"project_id": project_id}, order_by="created_at DESC", limit=1
            )
            run = runs[0] if runs else None

        if run and not decision:
            decisions = self.database.list(
                "decisions",
                {
                    "run_id": run["id"],
                    "stage": run.get("stage"),
                    "status": "pending",
                },
                order_by="created_at DESC",
                limit=1,
            )
            decision = decisions[0] if decisions else None

        if decision and (not run or decision["run_id"] != run["id"]):
            raise ValueError("CONVERSATION_DECISION_RUN_MISMATCH")

        actual_stage = str(run.get("stage") or "") if run else None
        decision_is_current = bool(
            decision
            and run
            and run.get("status") == "awaiting_decision"
            and decision.get("status") == "pending"
            and decision.get("stage") == run.get("stage")
        )
        public = {
            "selection": "explicit"
            if requested.get("run_id") or requested_decision_id
            else "latest",
            "run_id": str(run["id"]) if run else None,
            "stage": actual_stage,
            "decision_id": str(decision["id"]) if decision else None,
            "decision_kind": str(decision.get("kind") or "") if decision else None,
            "decision_is_current": decision_is_current,
            "stage_changed": bool(requested_stage and requested_stage != actual_stage),
        }
        return ConversationScopeSnapshot(public=public, run=run, decision=decision)

    def _history_snapshot(self, conversation_id: str) -> list[dict[str, Any]]:
        messages = self.database.list(
            "conversation_messages",
            {"conversation_id": conversation_id},
            order_by="created_at DESC",
            limit=8,
        )
        return list(reversed(messages))

    @staticmethod
    def _safe_history(
        messages: list[dict[str, Any]], aliases: dict[str, str]
    ) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for message in messages:
            content = str(message["content"])
            for original, alias in sorted(
                aliases.items(), key=lambda item: len(item[0]), reverse=True
            ):
                content = content.replace(original, alias)
            try:
                validate_provider_text(content)
            except ValueError:
                content = "<本地敏感内容已省略>"
            result.append({"role": message["role"], "content": content[:1500]})
        return result

    @staticmethod
    def _fallback_answer(
        run: dict[str, Any] | None, state: dict[str, Any], provider_block: str | None
    ) -> str:
        prefix = ""
        if provider_block:
            reason = public_error_message(
                normalize_error_code(provider_block, "PROVIDER_REQUEST_FAILED")
            )
            prefix = f"{reason}\n\n下面是仅基于本地项目状态生成的降级答复。\n\n"
        if not run:
            return (
                prefix
                + "项目已经创建，但还没有 Run。请先导入基准表/特征表，完成关联并创建一个 Y 任务。"
            )
        if run["status"] == "awaiting_decision":
            return (
                prefix
                + f"当前停在「{run['stage']}」确认节点。右侧阶段栏会展示 Reviewer 结论和可修改项；确认后会从同一 checkpoint 继续。"
            )
        if run["status"] == "succeeded":
            result = state.get("model_result") or {}
            metrics = result.get("champion_metrics") or {}
            test = metrics.get("test") or {}
            return (
                prefix
                + f"本次已完成，Champion 是 {result.get('champion', '—')}，Test AUC={_metric(test.get('roc_auc'))}、KS={_metric(test.get('ks'))}。可以进入产物报告或上传新样本批量评分。"
            )
        if run["status"] == "failed":
            failure = public_error_message(
                normalize_error_code(run.get("error"), "RUN_EXECUTION_FAILED")
            )
            return prefix + f"Run 在「{run['stage']} / {run['node']}」失败：{failure}"
        return (
            prefix
            + f"当前 Run 正在「{run['stage']} / {run['node']}」执行，进度约 {float(run.get('progress') or 0):.0%}。详细工具与证据引用会持续显示在右侧事件流。"
        )

    def _append_event(
        self,
        conversation_id: str,
        status: str,
        agent: str,
        content: str,
        summary: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock, self.database.transaction() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM conversation_events WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            event = {
                "id": new_id("cevt"),
                "conversation_id": conversation_id,
                "seq": int(row["next_seq"]),
                "status": status,
                "agent": agent,
                "content": content,
                "summary": summary,
                "evidence_json": evidence,
                "created_at": now_iso(),
            }
            encoded = self.database._encode(event)
            columns = ", ".join(encoded)
            placeholders = ", ".join("?" for _ in encoded)
            connection.execute(
                f"INSERT INTO conversation_events ({columns}) VALUES ({placeholders})",
                tuple(encoded.values()),
            )
        return self.database.get("conversation_events", event["id"]) or event

    def shutdown(self) -> None:
        # Workspace changes and app shutdown must not leave a response thread
        # writing conversation events into the old root after the new context
        # becomes active.
        self._executor.shutdown(wait=True, cancel_futures=False)


def _chunks(value: str, size: int) -> list[str]:
    return [value[index : index + size] for index in range(0, len(value), size)] or [""]


def _metric(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "—"


def _provider_unavailable_reason(settings: Any, gateway: ProviderGateway) -> str:
    """Return a stable reason code without ever exposing the configured secret."""
    if not settings.llm_enabled:
        return "LLM_DISABLED"
    if not gateway.key:
        return "PROVIDER_API_KEY_MISSING"
    return "PROVIDER_CONFIGURATION_INCOMPLETE"
