import { describe, expect, it } from "vitest";
import {
  providerConnectionState,
  providerModelUpdatePayload,
  type Settings,
} from "@/features/settings";

function settings(overrides: Partial<Settings> = {}): Settings {
  return {
    provider: "deepseek",
    api_format: "openai",
    base_url: "https://api.deepseek.com",
    model: "deepseek-chat",
    reviewer_model: "",
    llm_enabled: true,
    mode: "semi_trusted",
    run_token_budget: 0,
    monthly_token_budget: 0,
    proxy: "",
    ca_cert: "",
    notebook_network: true,
    telemetry: false,
    auto_update: true,
    memory_budget_mb: 1536,
    max_parallel_models: 1,
    default_models: [],
    api_key: "",
    api_key_configured: false,
    secret_storage: "not_configured",
    data_dir: "",
    synced_path_warning: false,
    ...overrides,
  };
}

describe("provider connection state", () => {
  it("keeps the active custom profile when changing only the model", () => {
    expect(providerModelUpdatePayload("deepseek-work", "deepseek-reasoner")).toEqual({
      profile_id: "deepseek-work",
      model: "deepseek-reasoner",
    });
  });

  it("explicitly reports an unconfigured API instead of implying an LLM connection", () => {
    const state = providerConnectionState(settings());
    expect(state.ready).toBe(false);
    expect(state.label).toBe("API 未连接");
    expect(state.description).toContain("本地降级");
  });

  it("uses the active profile as the authoritative connection state", () => {
    const state = providerConnectionState(
      settings({
        active_profile_id: "team",
        profiles: [
          {
            id: "team",
            label: "团队配置",
            provider: "deepseek",
            api_format: "openai",
            base_url: "https://api.deepseek.com",
            model: "deepseek-reasoner",
            reviewer_model: "",
            llm_enabled: true,
            api_key: "••••••••",
            api_key_configured: true,
            secret_storage: "local-protected-file",
            active: true,
          },
        ],
      }),
    );
    expect(state.ready).toBe(true);
    expect(state.label).toBe("API 已配置");
    expect(state.model).toBe("deepseek-reasoner");
  });
});
