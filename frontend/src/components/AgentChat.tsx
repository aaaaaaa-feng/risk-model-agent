import { useCallback, useEffect, useRef, useState } from "react";
import { api, eventUrl } from "../api";
import { errorMessage } from "../lib/format";
import { Markdown } from "./Markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ChatInput, ChatInputSubmit, ChatInputTextArea } from "@/components/ui/chat-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { notify } from "@/lib/notify";
import { providerConnectionState, providerModelUpdatePayload } from "@/lib/provider-state";
import { isAbortError, isCurrentChatRequest } from "@/lib/chat-request";
import { Hint } from "@/components/ui/hint";
import { RefreshCw } from "lucide-react";
import type { Message, Settings } from "../types";

interface ConversationResponse {
  conversation: { id: string };
  messages: Message[];
}

interface MessagePostResponse {
  conversation_id: string;
  response_id: string;
  user_message: Message;
}

/* 风控语境的快捷提问,点击填入输入框 */
const promptSuggestions = [
  "解释当前所处阶段与下一步",
  "检查数据资产的质量与风险",
  "汇总最新 Run 的模型效果",
  "审阅特征工程与分箱方案",
];

/* 各 Provider 的常用模型(与设置中心 preset 对齐,可在对话栏快速切换) */
const modelVariants: Record<string, string[]> = {
  deepseek: ["deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"],
  kimi: ["kimi-k2.6", "kimi-k2", "moonshot-v1-32k"],
  "kimi-code": ["kimi-for-coding"],
  openai: ["gpt-5", "gpt-5-mini", "gpt-4o"],
  anthropic: ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
  custom: [],
};

interface Props {
  projectId: string | null;
  settings: Settings | null;
  onProviderChange: () => void;
}

export function AgentChat({ projectId, settings, onProviderChange }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [pressed, setPressed] = useState<Record<string, string>>({});
  const scrollRef = useRef<HTMLDivElement>(null);
  const projectRef = useRef(projectId);
  const loadGenerationRef = useRef(0);
  const streamGenerationRef = useRef(0);
  const loadAbortRef = useRef<AbortController | null>(null);
  const submitAbortRef = useRef<AbortController | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const recoveryTimerRef = useRef<number | null>(null);
  projectRef.current = projectId;

  const clearRecoveryTimer = useCallback(() => {
    if (recoveryTimerRef.current !== null) {
      window.clearTimeout(recoveryTimerRef.current);
      recoveryTimerRef.current = null;
    }
  }, []);

  const closeEventSource = useCallback(() => {
    clearRecoveryTimer();
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
  }, [clearRecoveryTimer]);

  const invalidateAsyncRequests = useCallback(() => {
    loadGenerationRef.current += 1;
    streamGenerationRef.current += 1;
  }, []);

  const load = useCallback(async () => {
    const generation = ++loadGenerationRef.current;
    loadAbortRef.current?.abort();
    if (!projectId) {
      setMessages([]);
      return;
    }
    const requestedProjectId = projectId;
    const controller = new AbortController();
    loadAbortRef.current = controller;
    try {
      const value = await api.get<ConversationResponse>(
        `/projects/${requestedProjectId}/conversation`,
        { signal: controller.signal },
      );
      if (
        controller.signal.aborted ||
        generation !== loadGenerationRef.current ||
        projectRef.current !== requestedProjectId
      )
        return;
      setMessages(value.messages);
    } catch (error) {
      if (
        isAbortError(error) ||
        generation !== loadGenerationRef.current ||
        projectRef.current !== requestedProjectId
      )
        return;
      notify(errorMessage(error), true);
    } finally {
      if (loadAbortRef.current === controller) loadAbortRef.current = null;
    }
  }, [projectId]);

  useEffect(() => {
    ++streamGenerationRef.current;
    loadAbortRef.current?.abort();
    submitAbortRef.current?.abort();
    closeEventSource();
    setMessages([]);
    setInput("");
    setDraft("");
    setBusy(false);
    setPressed({});
    void load();
    return () => {
      invalidateAsyncRequests();
      loadAbortRef.current?.abort();
      submitAbortRef.current?.abort();
      closeEventSource();
    };
  }, [closeEventSource, invalidateAsyncRequests, load]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, draft]);

  const submit = async () => {
    if (!projectId || !input.trim() || busy) return;
    const requestedProjectId = projectId;
    const generation = ++streamGenerationRef.current;
    submitAbortRef.current?.abort();
    closeEventSource();
    const controller = new AbortController();
    submitAbortRef.current = controller;
    const content = input.trim();
    setInput("");
    setBusy(true);
    setDraft("");
    try {
      const result = await api.post<MessagePostResponse>(
        `/projects/${requestedProjectId}/conversation/messages`,
        {
          content,
        },
        { signal: controller.signal },
      );
      if (
        !isCurrentChatRequest(
          streamGenerationRef.current,
          generation,
          projectRef.current,
          requestedProjectId,
        )
      )
        return;
      setMessages((current) => [...current, result.user_message]);
      const source = new EventSource(
        eventUrl(
          `/conversations/${result.conversation_id}/events/stream?response_id=${encodeURIComponent(result.response_id)}`,
        ),
      );
      eventSourceRef.current = source;
      source.onopen = clearRecoveryTimer;
      source.addEventListener("conversation_event", (message) => {
        if (
          !isCurrentChatRequest(
            streamGenerationRef.current,
            generation,
            projectRef.current,
            requestedProjectId,
          )
        ) {
          source.close();
          return;
        }
        const item = JSON.parse((message as MessageEvent).data);
        if (item.evidence?.response_id !== result.response_id) return;
        if (item.status === "delta") setDraft((current) => current + item.content);
      });
      source.addEventListener("stream_end", async () => {
        if (
          !isCurrentChatRequest(
            streamGenerationRef.current,
            generation,
            projectRef.current,
            requestedProjectId,
          )
        ) {
          source.close();
          return;
        }
        closeEventSource();
        setDraft("");
        setBusy(false);
        await load();
      });
      source.onerror = () => {
        if (
          !isCurrentChatRequest(
            streamGenerationRef.current,
            generation,
            projectRef.current,
            requestedProjectId,
          )
        ) {
          source.close();
          return;
        }
        // EventSource 会携带 Last-Event-ID 自动重连。给短暂断网一个
        // 恢复窗口，避免一触发 error 就主动关闭并丢失 completed 事件。
        if (recoveryTimerRef.current === null) {
          recoveryTimerRef.current = window.setTimeout(() => {
            if (
              !isCurrentChatRequest(
                streamGenerationRef.current,
                generation,
                projectRef.current,
                requestedProjectId,
              )
            )
              return;
            closeEventSource();
            setBusy(false);
            setDraft("");
            void load();
            notify("对话事件流未能自动恢复，已重新读取已保存的回复。", true);
          }, 15_000);
        }
      };
    } catch (error) {
      if (
        isAbortError(error) ||
        !isCurrentChatRequest(
          streamGenerationRef.current,
          generation,
          projectRef.current,
          requestedProjectId,
        )
      )
        return;
      setBusy(false);
      notify(errorMessage(error), true);
    } finally {
      if (submitAbortRef.current === controller) submitAbortRef.current = null;
    }
  };

  const feedback = async (messageId: string, rating: string) => {
    try {
      await api.post(`/conversation-messages/${messageId}/feedback`, { rating });
      setPressed((current) => ({ ...current, [messageId]: rating }));
    } catch (error) {
      notify(errorMessage(error), true);
    }
  };

  /* 模型切换:已保存的 Provider 配置(activate)+ 当前 Provider 的常用模型(改 settings.model) */
  const profiles = settings?.profiles || [];
  const activeId = settings?.active_profile_id || settings?.provider || "";
  const connection = providerConnectionState(settings);
  const modelLabel = connection.model;
  const provider = connection.provider;
  const variants = modelVariants[provider] || [];
  const modelOptions = variants.includes(modelLabel)
    ? variants
    : [modelLabel, ...variants].filter(Boolean);
  const otherProfiles = profiles.filter((profile) => profile.id !== activeId);
  const canSwitch = modelOptions.length > 1 || otherProfiles.length > 0;

  const activate = async (profileId: string) => {
    if (profileId === activeId || switching) return;
    setSwitching(true);
    try {
      await api.post(`/providers/profiles/${profileId}/activate`);
      const target = profiles.find((profile) => profile.id === profileId);
      notify(`已切换模型配置：${target?.label || profileId}`);
      onProviderChange();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setSwitching(false);
    }
  };

  const switchModel = async (model: string) => {
    if (model === modelLabel || switching) return;
    setSwitching(true);
    try {
      await api.put("/providers/settings", providerModelUpdatePayload(activeId, model));
      notify(`已切换模型：${model}`);
      onProviderChange();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setSwitching(false);
    }
  };

  const onModelSelect = (value: string) => {
    if (value.startsWith("p:")) activate(value.slice(2));
    else if (value.startsWith("m:")) switchModel(value.slice(2));
  };

  const welcome = projectId && !messages.length && !busy && !draft;

  return (
    <section className="agent-chat" aria-label="项目 Agent 对话">
      <div className="chat-head">
        <strong>
          项目 Agent 对话
          <Hint text="多轮对话持久化保存；流式展示执行摘要；不展示隐藏思维链。" />
        </strong>
        <Badge
          variant={connection.ready ? "ok" : settings ? "attention" : "neutral"}
          title={connection.description}
          aria-live="polite"
        >
          {connection.label}
        </Badge>
      </div>
      <div className="chat-messages" ref={scrollRef}>
        {!projectId && <p className="chat-placeholder">创建或选择项目后开始对话。</p>}
        {projectId && settings && !connection.ready && (
          <p className="chat-api-state" role="status">
            <strong>{connection.label}</strong>
            <span>{connection.description}</span>
          </p>
        )}
        {welcome && (
          <Card className="chat-welcome">
            <CardContent className="flex flex-col items-start gap-3">
              <span className="eyebrow">MAIN AGENT</span>
              <h3>你好，我是本项目的建模 Agent</h3>
              <p>可以询问当前阶段、数据质量或模型效果，也可以从一个常见问题开始：</p>
              <div className="chat-prompts">
                {promptSuggestions.map((prompt) => (
                  <Button
                    key={prompt}
                    variant="outline"
                    size="sm"
                    className="prompt"
                    onClick={() => setInput(prompt)}
                  >
                    {prompt}
                  </Button>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
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
                    title="将这条 Agent 回复标记为有帮助"
                    aria-pressed={pressed[message.id] === "up"}
                  >
                    赞
                  </button>
                  <button
                    onClick={() => feedback(message.id, "down")}
                    aria-label="需要改进"
                    title="将这条 Agent 回复标记为需要改进"
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
              <p>
                {connection.ready
                  ? `正在请求 ${modelLabel}，并结合当前项目节点与 Reviewer 证据生成答复…`
                  : `${connection.label}，正在基于本地项目状态生成降级答复…`}
              </p>
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
          placeholder={projectId ? "补充要求或询问阶段…" : "请先选择项目"}
        />
        <div className="flex w-full items-center justify-between gap-2">
          {connection.ready && modelLabel ? (
            canSwitch ? (
              <Select value={`m:${modelLabel}`} onValueChange={onModelSelect} disabled={switching}>
                <SelectTrigger
                  aria-label="切换模型"
                  className="h-[30px] w-auto gap-1.5 rounded-full px-3 font-mono text-[10px]"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {modelOptions.map((model) => (
                    <SelectItem value={`m:${model}`} key={`m:${model}`}>
                      {model}
                    </SelectItem>
                  ))}
                  {otherProfiles.map((profile) => (
                    <SelectItem value={`p:${profile.id}`} key={`p:${profile.id}`}>
                      {profile.label}
                      {profile.model ? ` · ${profile.model}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Badge variant="muted">{modelLabel}</Badge>
            )
          ) : (
            <Badge variant="attention" title={connection.description}>
              {connection.label}
            </Badge>
          )}
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-[30px] w-[30px]"
              aria-label="重新加载对话"
              disabled={!projectId || busy}
              onClick={load}
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
            <ChatInputSubmit
              aria-label="发送"
              className="h-[30px] w-[30px] p-0 [&_svg]:h-3.5 [&_svg]:w-3.5"
            />
          </div>
        </div>
      </ChatInput>
    </section>
  );
}
