import type { ProviderProfile, Settings } from "../types";

export interface ProviderConnectionState {
  ready: boolean;
  label: string;
  description: string;
  model: string;
  provider: string;
  activeProfile?: ProviderProfile;
}

export function providerModelUpdatePayload(activeProfileId: string, model: string) {
  return { profile_id: activeProfileId, model };
}

export function providerConnectionState(settings: Settings | null): ProviderConnectionState {
  if (!settings) {
    return {
      ready: false,
      label: "正在读取 API 状态",
      description: "正在读取本机保存的模型与 API 配置。",
      model: "",
      provider: "",
    };
  }

  const profiles = settings.profiles || [];
  const activeId = settings.active_profile_id || settings.provider || "";
  const activeProfile = profiles.find((profile) => profile.id === activeId);
  const provider = activeProfile?.provider || settings.provider || "";
  const model = activeProfile?.model || settings.model || "";
  const baseUrl = activeProfile?.base_url || settings.base_url || "";
  const enabled = activeProfile?.llm_enabled ?? settings.llm_enabled;
  const keyConfigured = activeProfile?.api_key_configured ?? settings.api_key_configured;
  const configured = Boolean(keyConfigured && baseUrl && model);

  if (!configured) {
    return {
      ready: false,
      label: "API 未连接",
      description: "尚未完成 API 配置。当前提问只会得到明确标注的本地降级答复。",
      model,
      provider,
      activeProfile,
    };
  }
  if (!enabled) {
    return {
      ready: false,
      label: "LLM 已关闭",
      description: "API 配置已保存，但 LLM 调用已关闭。当前提问只使用本地降级。",
      model,
      provider,
      activeProfile,
    };
  }
  return {
    ready: true,
    label: "API 已配置",
    description: `当前提问将由 ${model} 生成；调用失败时会单独标注本地降级。`,
    model,
    provider,
    activeProfile,
  };
}
