export function isCurrentChatRequest(
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
