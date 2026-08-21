from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.agents.evidence import build_safe_evidence
from app.agents.prompts import CONVERSATION_PROMPT
from app.core.config import SettingsStore
from app.core.database import Database, new_id, now_iso
from app.core.paths import AppPaths, get_paths
from app.core.security import validate_provider_text
from app.providers.gateway import ProviderGateway

from .catalog import CatalogService


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

    def send(self, project_id: str, content: str) -> dict[str, Any]:
        cleaned = content.strip()
        if not cleaned:
            raise ValueError("MESSAGE_REQUIRED")
        self.catalog.get_project(project_id)
        conversation = self.catalog.ensure_conversation(project_id)
        user_message = self.catalog.add_message(project_id, "user", cleaned)
        response_id = new_id("response")
        self._append_event(
            conversation["id"],
            "queued",
            "main_agent",
            "",
            "已收到问题，正在读取当前项目状态",
            {"response_id": response_id},
        )
        self._executor.submit(
            self._answer, project_id, conversation["id"], response_id, cleaned
        )
        return {
            "user_message": user_message,
            "conversation_id": conversation["id"],
            "response_id": response_id,
        }

    def _answer(
        self, project_id: str, conversation_id: str, response_id: str, question: str
    ) -> None:
        self._append_event(
            conversation_id,
            "analyzing",
            "main_agent",
            "",
            "正在结合当前 Run、Reviewer 与待确认节点生成答复",
            {"response_id": response_id, "hidden_chain_of_thought_included": False},
        )
        runs = self.database.list("runs", {"project_id": project_id}, order_by="created_at DESC", limit=1)
        run = runs[0] if runs else None
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
            provider_block = str(exc)
        settings = SettingsStore(self.paths).load()
        gateway = ProviderGateway(settings=settings, paths=self.paths)
        answer = ""
        if gateway.enabled and not provider_block:
            history = self._safe_history(conversation_id, aliases)
            result = gateway.complete(
                CONVERSATION_PROMPT.content,
                {
                    "project_state": safe,
                    "current_run": {
                        "status": (run or {}).get("status"),
                        "stage": (run or {}).get("stage"),
                        "progress": (run or {}).get("progress"),
                    },
                    "conversation_history": history,
                    "question": safe_question,
                },
                purpose="project_conversation",
            )
            if result.ok:
                answer = result.content
            else:
                provider_block = result.error_code
        if not answer:
            answer = self._fallback_answer(run, state, provider_block)
        message = self.catalog.add_message(
            project_id,
            "assistant",
            answer,
            agent="main_agent",
            summary="基于当前项目状态的 Agent 答复",
        )
        for chunk in _chunks(answer, 36):
            self._append_event(
                conversation_id,
                "delta",
                "main_agent",
                chunk,
                "",
                {"response_id": response_id, "message_id": message["id"]},
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
            },
        )

    def _safe_history(
        self, conversation_id: str, aliases: dict[str, str]
    ) -> list[dict[str, str]]:
        messages = self.database.list(
            "conversation_messages",
            {"conversation_id": conversation_id},
            order_by="created_at DESC",
            limit=8,
        )
        result: list[dict[str, str]] = []
        for message in reversed(messages):
            content = str(message["content"])
            for original, alias in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
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
            prefix = f"这次没有把问题发送到外部 API（{provider_block}），下面使用本地状态回答。\n\n"
        if not run:
            return prefix + "项目已经创建，但还没有 Run。请先导入基准表/特征表，完成关联并创建一个 Y 任务。"
        if run["status"] == "awaiting_decision":
            return prefix + f"当前停在「{run['stage']}」确认节点。右侧阶段栏会展示 Reviewer 结论和可修改项；确认后会从同一 checkpoint 继续。"
        if run["status"] == "succeeded":
            result = state.get("model_result") or {}
            metrics = result.get("champion_metrics") or {}
            test = metrics.get("test") or {}
            return prefix + f"本次已完成，Champion 是 {result.get('champion', '—')}，Test AUC={_metric(test.get('roc_auc'))}、KS={_metric(test.get('ks'))}。可以进入产物报告或上传新样本批量评分。"
        if run["status"] == "failed":
            return prefix + f"Run 在「{run['stage']} / {run['node']}」失败，错误码是 {run.get('error') or 'UNKNOWN'}。该失败不会影响同项目的其他 Y 任务。"
        return prefix + f"当前 Run 正在「{run['stage']} / {run['node']}」执行，进度约 {float(run.get('progress') or 0):.0%}。详细工具与证据引用会持续显示在右侧事件流。"

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
        self._executor.shutdown(wait=False, cancel_futures=False)


def _chunks(value: str, size: int) -> list[str]:
    return [value[index : index + size] for index in range(0, len(value), size)] or [""]


def _metric(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "—"
