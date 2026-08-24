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
