export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  agent?: string | null;
  content: string;
  summary: string;
  created_at: string;
}

export interface ConversationResponse {
  conversation: { id: string };
  messages: Message[];
}

export interface MessagePostResponse {
  conversation_id: string;
  response_id: string;
  user_message: Message;
}
