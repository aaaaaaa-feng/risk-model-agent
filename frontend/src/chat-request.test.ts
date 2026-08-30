import { describe, expect, it } from "vitest";
import {
  isAbortError,
  isCurrentChatRequest,
  parseConversationEvent,
} from "@/features/chat/lib/chatRequest";

describe("project-scoped chat requests", () => {
  it("rejects an old load or stream after the project or request generation changes", () => {
    expect(isCurrentChatRequest(3, 3, "project_b", "project_b")).toBe(true);
    expect(isCurrentChatRequest(4, 3, "project_b", "project_b")).toBe(false);
    expect(isCurrentChatRequest(3, 3, "project_a", "project_b")).toBe(false);
  });

  it("recognizes an AbortController cancellation without treating other failures as aborts", () => {
    const controller = new AbortController();
    const request = new Request("http://localhost", { signal: controller.signal });
    controller.abort();
    expect(request.signal.aborted).toBe(true);
    expect(isAbortError(new DOMException("cancelled", "AbortError"))).toBe(true);
    expect(isAbortError(new Error("network"))).toBe(false);
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
