import type { ChatContext } from "../types";

export function chatContextKey(context: ChatContext): string {
  return JSON.stringify([context.run_id || "", context.stage || "", context.decision_id || ""]);
}

export function isCurrentChatRequest(
  activeGeneration: number,
  requestGeneration: number,
  activeProjectId: string | null,
  requestProjectId: string,
  activeContextKey: string,
  requestContextKey: string,
): boolean {
  return (
    activeGeneration === requestGeneration &&
    activeProjectId === requestProjectId &&
    activeContextKey === requestContextKey
  );
}

/**
 * 已被后端接受的请求要跟踪到完成，即使 Run 在回答期间跨了阶段。
 * transport 只绑定项目和请求代次；contextKey 只决定旧草稿能否显示。
 */
export function isCurrentChatTransport(
  activeGeneration: number,
  requestGeneration: number,
  activeProjectId: string | null,
  requestProjectId: string,
): boolean {
  return activeGeneration === requestGeneration && activeProjectId === requestProjectId;
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export interface ConversationStreamEvent {
  status: string;
  content: string;
  evidence: { response_id: string };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 对话 SSE 来自运行时边界，损坏 JSON 或缺字段时返回 null。 */
export function parseConversationEvent(data: unknown): ConversationStreamEvent | null {
  let value: unknown = data;
  try {
    value = typeof data === "string" ? JSON.parse(data) : data;
  } catch {
    return null;
  }
  if (
    !isRecord(value) ||
    typeof value.status !== "string" ||
    !isRecord(value.evidence) ||
    typeof value.evidence.response_id !== "string" ||
    (value.status === "delta" && typeof value.content !== "string")
  )
    return null;
  return {
    status: value.status,
    content: typeof value.content === "string" ? value.content : "",
    evidence: { response_id: value.evidence.response_id },
  };
}
