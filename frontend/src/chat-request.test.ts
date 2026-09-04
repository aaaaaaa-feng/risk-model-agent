import { describe, expect, it } from "vitest";
import {
  chatContextKey,
  isAbortError,
  isCurrentChatRequest,
  isCurrentChatTransport,
  parseConversationEvent,
} from "@/features/chat/lib/chatRequest";

const agentChatSource = import.meta.glob("./features/chat/ui/AgentChat.tsx", {
  eager: true,
  query: "?raw",
  import: "default",
})["./features/chat/ui/AgentChat.tsx"] as string;

describe("project-scoped chat requests", () => {
  it("rejects an old load or stream after the project, context or generation changes", () => {
    const runA = chatContextKey({ run_id: "run_a", stage: "binning", decision_id: null });
    const runB = chatContextKey({ run_id: "run_b", stage: "training", decision_id: null });

    expect(isCurrentChatRequest(3, 3, "project_b", "project_b", runB, runB)).toBe(true);
    expect(isCurrentChatRequest(4, 3, "project_b", "project_b", runB, runB)).toBe(false);
    expect(isCurrentChatRequest(3, 3, "project_a", "project_b", runB, runB)).toBe(false);
    expect(isCurrentChatRequest(3, 3, "project_b", "project_b", runB, runA)).toBe(false);
  });

  it("recognizes an AbortController cancellation without treating other failures as aborts", () => {
    const controller = new AbortController();
    const request = new Request("http://localhost", { signal: controller.signal });
    controller.abort();
    expect(request.signal.aborted).toBe(true);
    expect(isAbortError(new DOMException("cancelled", "AbortError"))).toBe(true);
    expect(isAbortError(new Error("network"))).toBe(false);
  });

  it("同项目跨阶段时继续跟踪后端完成，但不再显示旧上下文草稿", () => {
    const requested = chatContextKey({ run_id: "run_a", stage: "binning", decision_id: null });
    const current = chatContextKey({ run_id: "run_a", stage: "training", decision_id: null });

    expect(isCurrentChatTransport(3, 3, "project_a", "project_a")).toBe(true);
    expect(isCurrentChatRequest(3, 3, "project_a", "project_a", current, requested)).toBe(false);
    expect(isCurrentChatTransport(4, 3, "project_a", "project_a")).toBe(false);
    expect(agentChatSource).toContain("activeRequestDetachedRef.current = true");
    expect(agentChatSource).toContain("await load()");
  });

  it("对话事件损坏时安全返回 null，不抛出解析异常", () => {
    expect(parseConversationEvent("{not-json")).toBeNull();
    expect(parseConversationEvent(JSON.stringify({ status: "delta", evidence: {} }))).toBeNull();
    expect(
      parseConversationEvent(
        JSON.stringify({
          status: "delta",
          content: "正在分析",
          evidence: { response_id: "response-1" },
        }),
      ),
    ).toEqual({
      status: "delta",
      content: "正在分析",
      evidence: { response_id: "response-1" },
    });
  });
});
