export type ErrorContext = "default" | "workspace" | "provider" | "decision" | "model" | "review";

export interface ErrorTranslationOptions {
  context?: ErrorContext;
}

export interface FriendlyError {
  summary: string;
  action: string;
  text: string;
  code?: string;
}

interface ErrorFacts {
  status?: number;
  code?: string;
  message?: string;
}

const CONTEXT_FALLBACKS: Record<ErrorContext, Pick<FriendlyError, "summary" | "action">> = {
  default: {
    summary: "操作没有完成。",
    action: "请重试；如果仍然失败，请刷新页面或重启应用。",
  },
  workspace: {
    summary: "工作文件夹操作没有完成。",
    action: "请检查路径和读写权限，或换一个本机文件夹后重试。",
  },
  provider: {
    summary: "模型 API 请求没有成功。",
    action: "请检查 API 密钥、Base URL、模型名称和网络后重试。",
  },
  decision: {
    summary: "当前确认没有提交成功。",
    action: "请刷新当前 Run，确认节点仍在等待后重试。",
  },
  model: {
    summary: "候选模型训练没有成功。",
    action: "请查看该候选模型的运行证据，调整数据或资源设置后重试。",
  },
  review: {
    summary: "Reviewer 发现一项需要处理的问题。",
    action: "请根据当前阶段建议调整方案，再重新提交确认。",
  },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function errorFacts(error: unknown): ErrorFacts {
  if (typeof error === "string")
    return {
      code: error.match(/^([A-Z][A-Z0-9_]{2,})(?::|\s|$)/)?.[1],
      message: error,
    };
  if (!isRecord(error)) return {};
  const status = typeof error.status === "number" ? error.status : undefined;
  const rawCode = stringValue(error.code) || stringValue(error.error_code);
  const explicitCode = rawCode?.match(/^([A-Z][A-Z0-9_]{2,})(?::|\s|$)/)?.[1];
  const message = error instanceof Error ? error.message : stringValue(error.message);
  const messageCode = message?.match(/^([A-Z][A-Z0-9_]{2,})(?::|$)/)?.[1];
  return { status, code: explicitCode || messageCode, message };
}

function diagnosticCode(value: string | undefined): string | undefined {
  if (!value || value.startsWith("HTTP_")) return undefined;
  return /^[A-Z][A-Z0-9_]{2,80}$/.test(value) ? value : undefined;
}

function safeChineseMessage(value: string | undefined): string | undefined {
  if (!value || !/[\u3400-\u9fff]/.test(value)) return undefined;
  if (
    /(?:Traceback|stack trace|\bat\s+\S+\s*\(|File\s+"[^"]+",\s*line\s+\d+|[A-Za-z]+(?:Error|Exception):)/i.test(
      value,
    ) ||
    /(?:\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,}|(?:api[_ -]?key|token|secret|password|密钥|令牌|密码)\s*[:=：]\s*\S+|\b1[3-9]\d{9}\b|\b(?:\d{15}|\d{16,19}|\d{17}[Xx])\b|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})/i.test(
      value,
    ) ||
    /(?:^|[^A-Za-z0-9:/])(?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/]|~[\\/]|\/(?!\/)[^\s，。；："'<>]+)/.test(
      value,
    )
  )
    return undefined;
  const cleaned = value
    .replace(/^['"]|['"]$/g, "")
    .replace(/^[A-Z][A-Z0-9_]{2,80}\s*:\s*/, "")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned && cleaned !== "[object Object]" ? cleaned.slice(0, 220) : undefined;
}

function hasSuggestedAction(value: string): boolean {
  return /(?:请|可以|建议|重试|检查|选择|刷新|重启|稍后)/.test(value);
}

function sentence(value: string): string {
  const trimmed = value.trim();
  return /[。！？.!?]$/.test(trimmed) ? trimmed : `${trimmed}。`;
}

function ruleFor(
  facts: ErrorFacts,
  context: ErrorContext,
): Pick<FriendlyError, "summary" | "action"> {
  const code = facts.code || "";
  const message = (facts.message || "").toLowerCase();

  if (
    facts.status === 0 ||
    /failed to fetch|networkerror|network request failed|load failed|econnrefused|connection refused/.test(
      message,
    )
  )
    return {
      summary: "无法连接本地服务。",
      action: "请确认应用仍在运行，然后刷新页面重试。",
    };
  if (/timeout|timed out/i.test(message) || code.includes("TIMEOUT"))
    return {
      summary: "等待处理结果超时。",
      action: "请稍后重试；数据量较大时可减少单次任务规模。",
    };
  if (code.startsWith("WORKSPACE_NATIVE_PICKER_"))
    return {
      summary: "系统文件夹选择器未能正常完成。",
      action: "请重试；仍无响应时，可直接输入完整文件夹路径。",
    };
  if (code.startsWith("WORKSPACE_PATH_")) return CONTEXT_FALLBACKS.workspace;
  if (code === "WORKSPACE_SWITCH_ACTIVE_RUNS")
    return {
      summary: "当前工作区仍有任务在运行。",
      action: "请等待任务结束后再更换工作文件夹。",
    };
  if (code === "WORKSPACE_SWITCH_REQUIRES_EMPTY_CURRENT_PROJECTS")
    return {
      summary: "当前工作区已有项目，不能直接切换。",
      action: "请继续使用当前目录，或先完成项目迁移再重试。",
    };
  if (/^(?:PROVIDER_|EMPTY_API_KEY|API_FORMAT_INVALID)/.test(code)) {
    if (/AUTH|API_KEY/.test(code))
      return {
        summary: "API 密钥未通过验证。",
        action: "请重新填写密钥，并确认密钥对当前模型有访问权限。",
      };
    if (/RATE_LIMITED|BUDGET_EXCEEDED/.test(code))
      return {
        summary: "模型 API 当前受到频率或额度限制。",
        action: "请稍后重试，并检查账户余额、频率限制和 Token 预算。",
      };
    if (code === "PROVIDER_DISABLED")
      return {
        summary: "LLM 当前没有启用。",
        action: "请到设置中心启用一个已配置密钥的模型 API。",
      };
    if (/SCHEMA_INVALID|EMPTY_RESPONSE/.test(code))
      return {
        summary: "模型 API 返回的内容无法识别。",
        action: "请确认 API 格式和模型名称匹配，再测试连接。",
      };
    return CONTEXT_FALLBACKS.provider;
  }
  if (code === "DLP_BLOCK" || code.includes("PII") || code.includes("SECRET_FORBIDDEN"))
    return {
      summary: "安全检查阻止了这次请求。",
      action: "请移除原始数据、个人信息或密钥，只使用可出站的聚合证据。",
    };
  if (
    /LOCAL_SESSION|CROSS_ORIGIN|CROSS_SITE|LOCAL_HOST/.test(code) ||
    facts.status === 401 ||
    facts.status === 403
  )
    return {
      summary: "当前操作未通过本机安全校验。",
      action: "请从本机应用页面重新进入，刷新后再试。",
    };
  if (code === "USER_REJECTED")
    return {
      summary: "你已选择不批准当前方案。",
      action: "可以调整方案后，基于同一个 Y 新建 Run。",
    };
  if (/DECISION_NOT_PENDING|RUN_NOT_AWAITING_DECISION|DECISION_RESPONSE_INVALID/.test(code))
    return CONTEXT_FALLBACKS.decision;
  if (code === "RUN_EVENT_STREAM_INTERRUPTED")
    return {
      summary: "运行进度的实时连接暂时中断。",
      action: "已切换到普通刷新，并会自动重连。",
    };
  if (code === "RUN_EVENT_STREAM_INVALID")
    return {
      summary: "收到的一条运行进度无法识别。",
      action: "已忽略该条并重新读取最新运行状态。",
    };
  if (code === "RUN_EVENT_STREAM_STOPPED")
    return {
      summary: "运行进度的实时连接暂时无法恢复。",
      action: "已切换到普通刷新，并会低频尝试恢复实时连接。",
    };
  if (code === "CONVERSATION_EVENT_STREAM_INVALID")
    return {
      summary: "收到的一条对话进度无法识别。",
      action: "已停止本次流式展示，并重新读取已保存的回复。",
    };
  if (code === "REPORT_PREVIEW_POPUP_BLOCKED")
    return {
      summary: "浏览器阻止了报告预览窗口。",
      action: "请允许本地页面打开新窗口，再重试。",
    };
  if (/MODEL_|NO_AVAILABLE_MODELS|NO_SUCCESSFUL_MODELS|CHAMPION_MISSING/.test(code))
    return CONTEXT_FALLBACKS.model;
  if (facts.status === 404 || /(?:RESOURCE|PROJECT|TARGET)_NOT_FOUND/.test(code))
    return {
      summary: "请求的项目、数据或运行记录已不存在。",
      action: "请刷新项目列表，再重新选择。",
    };
  if (facts.status === 409)
    return {
      summary: "当前状态已发生变化，这次操作没有执行。",
      action: "请刷新页面，确认最新状态后再重试。",
    };
  if (facts.status === 413)
    return {
      summary: "文件或单次请求超过当前大小上限。",
      action: "请减小文件、分批处理，或在资源设置中调整上限后重试。",
    };
  if (facts.status === 429)
    return {
      summary: "当前请求过于频繁或服务额度受限。",
      action: "请稍后重试，并检查相关 API 的额度和频率限制。",
    };
  if (facts.status === 400 || facts.status === 422 || /(?:INVALID|REQUIRED)$/.test(code))
    return {
      summary: "提交的内容不符合当前要求。",
      action: "请检查必填项、字段格式和数据列后重试。",
    };
  if (facts.status && facts.status >= 500)
    return {
      summary: "本地服务处理时发生异常。",
      action: "请重试；如果仍然失败，请重启应用并查看运行日志。",
    };
  return CONTEXT_FALLBACKS[context];
}

export function translateError(
  error: unknown,
  options: ErrorTranslationOptions = {},
): FriendlyError {
  const context = options.context || "default";
  const facts = errorFacts(error);
  const rule = ruleFor(facts, context);
  const safeMessage = safeChineseMessage(facts.message);
  const summary = sentence(safeMessage || rule.summary);
  const action = safeMessage && hasSuggestedAction(safeMessage) ? "" : sentence(rule.action);
  return {
    summary,
    action,
    text: `${summary}${action ? ` ${action}` : ""}`,
    code: diagnosticCode(facts.code),
  };
}

export function errorMessage(error: unknown, options?: ErrorTranslationOptions): string {
  return translateError(error, options).text;
}

/**
 * Run 事件成功时保留原有审计摘要；失败时收敛为可操作的中文说明。
 * 技术码仍可由调用方放在 title 或审计记录中，不进入主文案。
 */
export function eventSummary(
  status: string | undefined,
  summary: string | undefined,
  code?: string,
): string {
  if (!["failed", "blocked"].includes(status || "")) return summary || "等待节点事件";
  return errorMessage({ code, message: summary });
}
