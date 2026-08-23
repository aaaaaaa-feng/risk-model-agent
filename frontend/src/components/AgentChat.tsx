import { useCallback, useEffect, useRef, useState } from "react";
import { api, eventUrl } from "../api";
import { errorMessage } from "../lib/format";
import { Markdown } from "./Markdown";
import { ChatInput, ChatInputSubmit, ChatInputTextArea } from "@/components/ui/chat-input";
import { notify } from "@/lib/notify";
import type { Message } from "../types";

interface ConversationResponse {
  conversation: { id: string };
  messages: Message[];
}

interface MessagePostResponse {
  conversation_id: string;
  response_id: string;
  user_message: Message;
}

export function AgentChat({ projectId }: { projectId: string | null }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const load = useCallback(async () => {
    if (!projectId) {
      setMessages([]);
      return;
    }
    try {
      const value = await api.get<ConversationResponse>(`/projects/${projectId}/conversation`);
      setMessages(value.messages);
    } catch (error) {
      notify(errorMessage(error), true);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, draft]);

  const submit = async () => {
    if (!projectId || !input.trim() || busy) return;
    const content = input.trim();
    setInput("");
    setBusy(true);
    setDraft("");
    try {
      const result = await api.post<MessagePostResponse>(
        `/projects/${projectId}/conversation/messages`,
        {
          content,
        },
      );
      setMessages((current) => [...current, result.user_message]);
      const source = new EventSource(
        eventUrl(
          `/conversations/${result.conversation_id}/events/stream?response_id=${encodeURIComponent(result.response_id)}`,
        ),
      );
      source.addEventListener("conversation_event", (message) => {
        const item = JSON.parse((message as MessageEvent).data);
        if (item.status === "delta") setDraft((current) => current + item.content);
      });
      source.addEventListener("stream_end", async () => {
        source.close();
        setDraft("");
        setBusy(false);
        await load();
      });
      source.onerror = () => {
        source.close();
        setBusy(false);
        notify("对话事件流断开，可重新发送或刷新恢复。", true);
      };
    } catch (error) {
      setBusy(false);
      notify(errorMessage(error), true);
    }
  };

  const [pressed, setPressed] = useState<Record<string, string>>({});

  const feedback = async (messageId: string, rating: string) => {
    try {
      await api.post(`/conversation-messages/${messageId}/feedback`, { rating });
      setPressed((current) => ({ ...current, [messageId]: rating }));
    } catch (error) {
      notify(errorMessage(error), true);
    }
  };

  return (
    <section className="agent-chat" aria-label="项目 Agent 对话">
      <div className="chat-head">
        <strong>项目 Agent 对话</strong>
        <span>多轮持久化 · 流式执行摘要 · 不展示隐藏思维链</span>
      </div>
      <div className="chat-messages" ref={scrollRef}>
        {!projectId && <p className="chat-placeholder">创建或选择项目后开始对话。</p>}
        {messages.map((message) =>
          message.role === "user" ? (
            <div className="chat-row user" key={message.id}>
              <div className="chat-bubble">
                <p>{message.content}</p>
              </div>
              <span className="chat-avatar user" aria-hidden>
                我
              </span>
            </div>
          ) : (
            <div className="chat-row assistant" key={message.id}>
              <span className="chat-avatar" aria-hidden>
                A
              </span>
              <div className="chat-bubble">
                <span className="chat-meta">{message.agent?.replace("_", " ") || "AGENT"}</span>
                <Markdown>{message.content}</Markdown>
                <div className="message-feedback">
                  <button
                    onClick={() => feedback(message.id, "up")}
                    aria-label="有帮助"
                    aria-pressed={pressed[message.id] === "up"}
                  >
                    赞
                  </button>
                  <button
                    onClick={() => feedback(message.id, "down")}
                    aria-label="需要改进"
                    aria-pressed={pressed[message.id] === "down"}
                  >
                    踩
                  </button>
                </div>
              </div>
            </div>
          ),
        )}
        {draft && (
          <div className="chat-row assistant streaming">
            <span className="chat-avatar" aria-hidden>
              A
            </span>
            <div className="chat-bubble">
              <span className="chat-meta">MAIN AGENT</span>
              <Markdown>{draft}</Markdown>
              <i className="cursor" aria-hidden />
            </div>
          </div>
        )}
        {busy && !draft && (
          <div className="chat-row assistant">
            <span className="chat-avatar" aria-hidden>
              A
            </span>
            <div className="chat-bubble typing">
              <span className="chat-meta">MAIN AGENT</span>
              <p>正在读取当前项目节点与 Reviewer 证据…</p>
            </div>
          </div>
        )}
      </div>
      <ChatInput
        className="chat-form"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onSubmit={submit}
        loading={busy}
      >
        <ChatInputTextArea
          aria-label="给 Agent 发送消息"
          disabled={!projectId || busy}
          placeholder={projectId ? "补充业务要求，或询问当前阶段…" : "请先选择项目"}
        />
        <ChatInputSubmit aria-label="发送" />
      </ChatInput>
    </section>
  );
}
