import { eventUrl, httpClient } from "@/shared/api/client";
import type { ChatContext, ConversationResponse, MessagePostResponse } from "../types";

export const chatApi = {
  conversation: (projectId: string, signal?: AbortSignal) =>
    httpClient.get<ConversationResponse>(
      `/projects/${encodeURIComponent(projectId)}/conversation`,
      { signal },
    ),
  send: (projectId: string, content: string, context: ChatContext, signal?: AbortSignal) =>
    httpClient.post<MessagePostResponse>(
      `/projects/${encodeURIComponent(projectId)}/conversation/messages`,
      { content, context },
      { signal },
    ),
  feedback: (messageId: string, rating: string) =>
    httpClient.post(`/conversation-messages/${encodeURIComponent(messageId)}/feedback`, { rating }),
  eventStreamUrl: (conversationId: string, responseId: string) =>
    eventUrl(
      `/conversations/${encodeURIComponent(conversationId)}/events/stream?response_id=${encodeURIComponent(responseId)}`,
    ),
};
