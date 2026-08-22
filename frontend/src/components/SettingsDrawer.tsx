import { FormEvent, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import type { ProviderProfile, Settings, WorkspaceStatus } from "../types";

interface Props {
  open: boolean;
  settings: Settings | null;
  workspace: WorkspaceStatus | null;
  onClose: () => void;
  onChanged: () => void;
  onChangeWorkspace: () => void;
  notify: (message: string, error?: boolean) => void;
}

type SectionId = "providers" | "modeling" | "network" | "workspace" | "backup";

const sections: Array<{ id: SectionId; label: string; hint: string }> = [
  { id: "providers", label: "模型与 API", hint: "Provider、密钥、Reviewer" },
  { id: "modeling", label: "建模默认值", hint: "算法与资源预算" },
  { id: "network", label: "网络与更新", hint: "Notebook、代理、遥测" },
  { id: "workspace", label: "工作文件夹", hint: "本地数据目录" },
  { id: "backup", label: "备份与重置", hint: "恢复与数据保护" },
];

const models = [
  "dummy",
  "scorecard",
  "regularized_logistic",
  "random_forest",
  "extra_trees",
  "xgboost",
  "lightgbm",
  "catboost",
];
const providerPresets: Record<string, { api_format: string; base_url: string; model: string }> = {
  deepseek: {
    api_format: "openai",
    base_url: "https://api.deepseek.com",
    model: "deepseek-v4-flash",
  },
  kimi: { api_format: "openai", base_url: "https://api.moonshot.cn/v1", model: "kimi-k2.6" },
  "kimi-code": {
    api_format: "openai",
    base_url: "https://api.kimi.com/coding/v1",
    model: "kimi-for-coding",
  },
  openai: { api_format: "openai", base_url: "https://api.openai.com/v1", model: "gpt-5" },
  anthropic: {
    api_format: "anthropic",
    base_url: "https://api.anthropic.com",
    model: "claude-sonnet-4-5",
  },
  custom: { api_format: "openai", base_url: "", model: "" },
};

export function SettingsDrawer({
  open,
  settings,
  workspace,
  onClose,
  onChanged,
  onChangeWorkspace,
  notify,
}: Props) {
  const [form, setForm] = useState<Record<string, any>>({});
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState("");
  const [testResult, setTestResult] = useState("");
  const [backups, setBackups] = useState<any[]>([]);
  const [section, setSection] = useState<SectionId>("providers");
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    if (!settings) return;
    const activeId = settings.active_profile_id || settings.provider;
    const active = (settings.profiles || []).find((profile) => profile.id === activeId);
    setForm({ ...settings, ...(active || {}), profile_id: activeId });
    setApiKey("");
    setClearKey(false);
  }, [settings]);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    if (!open) return;
    previousFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    window.setTimeout(() => closeRef.current?.focus(), 0);
    api
      .get<{ backups: any[] }>("/backups")
      .then((value) => setBackups(value.backups))
      .catch(() => undefined);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const focusable = Array.from(
        drawerRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousFocusRef.current?.focus();
    };
  }, [open]);

  if (!open || !settings) return null;
  const profiles = settings.profiles || [];
  const change = (key: string, value: any) => setForm((current) => ({ ...current, [key]: value }));
  const selectProfile = (profile: ProviderProfile) => {
    setForm((current) => ({ ...current, ...profile, profile_id: profile.id }));
    setApiKey("");
    setClearKey(false);
    setTestResult("");
  };
  const selectProvider = (provider: string) => {
    const existing = profiles.find((profile) => profile.provider === provider);
    if (existing) {
      selectProfile(existing);
      return;
    }
    const preset = providerPresets[provider];
    setForm((current) => ({
      ...current,
      provider,
      profile_id: provider,
      ...(preset || {}),
      api_key_configured: false,
      secret_storage: "not_configured",
    }));
    setApiKey("");
    setClearKey(false);
    setTestResult("");
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("save");
    try {
      await api.put("/providers/settings", {
        profile_id: form.profile_id || form.provider,
        provider: form.provider,
        api_format: form.api_format,
        base_url: form.base_url,
        model: form.model,
        reviewer_model: form.reviewer_model,
        llm_enabled: Boolean(form.llm_enabled),
        mode: form.mode,
        run_token_budget: form.run_token_budget,
        monthly_token_budget: form.monthly_token_budget,
        proxy: form.proxy,
        ca_cert: form.ca_cert,
        notebook_network: Boolean(form.notebook_network),
        telemetry: Boolean(form.telemetry),
        auto_update: Boolean(form.auto_update),
        memory_budget_mb: form.memory_budget_mb,
        max_parallel_models: form.max_parallel_models,
        default_models: form.default_models,
        api_key: apiKey || undefined,
        clear_api_key: clearKey,
      });
      setApiKey("");
      setClearKey(false);
      notify("设置已保存；Provider 配置已持久化");
      onChanged();
    } catch (error) {
      notify(error instanceof Error ? error.message : "保存失败", true);
    } finally {
      setBusy("");
    }
  };

  const test = async () => {
    setBusy("test");
    setTestResult("正在测试…");
    try {
      const result = await api.post<any>("/providers/test", {
        profile_id: form.profile_id || form.provider,
        provider: form.provider,
        api_format: form.api_format,
        base_url: form.base_url,
        model: form.model,
        reviewer_model: form.reviewer_model,
        llm_enabled: true,
        api_key: apiKey || undefined,
      });
      setTestResult(result.ok ? `连接成功 · ${result.model}` : `连接失败 · ${result.error_code}`);
    } catch (error) {
      setTestResult(error instanceof Error ? error.message : "连接失败");
    } finally {
      setBusy("");
    }
  };

  const createBackup = async () => {
    setBusy("backup");
    try {
      await api.post("/backups");
      const result = await api.get<{ backups: any[] }>("/backups");
      setBackups(result.backups);
      notify("本地数据库备份已创建");
    } catch (error) {
      notify(error instanceof Error ? error.message : "备份失败", true);
    } finally {
      setBusy("");
    }
  };

  const reset = async () => {
    if (!window.confirm("恢复默认设置？项目和数据不会被删除，API Key 默认保留。")) return;
    await api.post("/system/reset-settings", { confirm: true, clear_api_key: false });
    notify("已恢复默认设置");
    onChanged();
  };

  return (
    <>
      <div className="drawer-scrim" onMouseDown={onClose} />
      <aside
        ref={drawerRef}
        className="settings-drawer open"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
      >
        <div className="drawer-head">
          <div>
            <span className="eyebrow">SETTINGS</span>
            <h2 id="settings-title">设置中心</h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="icon-button inverse"
            onClick={onClose}
            aria-label="关闭设置"
          >
            ×
          </button>
        </div>
        <form onSubmit={save} className="settings-body">
          <div className="settings-layout">
            <nav className="settings-nav" aria-label="设置菜单">
              {sections.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`settings-nav-item ${section === item.id ? "active" : ""}`}
                  aria-current={section === item.id ? "page" : undefined}
                  onClick={() => setSection(item.id)}
                >
                  <strong>{item.label}</strong>
                  <span>{item.hint}</span>
                </button>
              ))}
            </nav>
            <div className="settings-panel">
              {section === "providers" && (
                <SettingsSection
                  title="模型与 API"
                  description="配置可持久化保存；只有 SafeEvidence 可以发送到外部 API。"
                >
                  <div className="provider-profiles">
                    <div className="provider-profiles-head">
                      <strong>已保存的 Provider 配置</strong>
                      <span>选择后编辑并保存</span>
                    </div>
                    {profiles.length ? (
                      profiles.map((profile) => (
                        <label
                          className={`provider-profile ${form.profile_id === profile.id ? "active" : ""}`}
                          key={profile.id}
                        >
                          <input
                            type="radio"
                            name="provider-profile"
                            checked={form.profile_id === profile.id}
                            onChange={() => selectProfile(profile)}
                          />
                          <span className="provider-profile-copy">
                            <strong>{profile.label}</strong>
                            <small>{profile.model || "未填写模型"}</small>
                          </span>
                          <span
                            className={`provider-profile-state ${profile.llm_enabled && profile.api_key_configured ? "on" : ""}`}
                          >
                            {profile.llm_enabled
                              ? profile.api_key_configured
                                ? "已启用"
                                : "待配置密钥"
                              : "已停用"}
                          </span>
                        </label>
                      ))
                    ) : (
                      <p className="empty-hint">
                        还没有保存的 Provider，选择下方 Provider 并保存即可创建。
                      </p>
                    )}
                  </div>
                  <label>
                    Provider
                    <select
                      value={form.provider || "deepseek"}
                      onChange={(event) => selectProvider(event.target.value)}
                    >
                      <option value="deepseek">DeepSeek</option>
                      <option value="kimi">Kimi 开放平台</option>
                      <option value="kimi-code">Kimi Code</option>
                      <option value="openai">OpenAI</option>
                      <option value="anthropic">Anthropic</option>
                      <option value="custom">自定义</option>
                    </select>
                  </label>
                  <div className="form-two">
                    <label>
                      API 格式
                      <select
                        value={form.api_format || "openai"}
                        onChange={(event) => change("api_format", event.target.value)}
                      >
                        <option value="openai">OpenAI Chat Completions</option>
                        <option value="anthropic">Anthropic Messages</option>
                      </select>
                    </label>
                    <label>
                      启用 LLM
                      <span className="switch-row">
                        <input
                          type="checkbox"
                          checked={Boolean(form.llm_enabled)}
                          onChange={(event) => change("llm_enabled", event.target.checked)}
                        />
                        允许聚合证据出站
                      </span>
                    </label>
                  </div>
                  <label>
                    Base URL
                    <input
                      value={form.base_url || ""}
                      onChange={(event) => change("base_url", event.target.value)}
                    />
                  </label>
                  <div className="form-two">
                    <label>
                      主模型
                      <input
                        value={form.model || ""}
                        onChange={(event) => change("model", event.target.value)}
                      />
                    </label>
                    <label>
                      Reviewer 模型
                      <input
                        value={form.reviewer_model || ""}
                        onChange={(event) => change("reviewer_model", event.target.value)}
                        placeholder="留空则同主模型"
                      />
                    </label>
                  </div>
                  <label>
                    API Key
                    <input
                      type="password"
                      autoComplete="new-password"
                      value={apiKey}
                      onChange={(event) => {
                        setApiKey(event.target.value);
                        if (event.target.value) setClearKey(false);
                      }}
                      disabled={form.secret_storage === "environment"}
                      placeholder={
                        form.api_key_configured ? "已配置；留空表示保持不变" : "输入密钥"
                      }
                    />
                  </label>
                  {form.secret_storage === "environment" ? (
                    <p className="inline-warning">
                      密钥来自环境变量，请在系统环境中移除；页面不能伪装成已清除。
                    </p>
                  ) : (
                    <label className="check-row">
                      <input
                        type="checkbox"
                        checked={clearKey}
                        onChange={(event) => {
                          setClearKey(event.target.checked);
                          if (event.target.checked) setApiKey("");
                        }}
                      />
                      保存时清除当前配置的 API Key
                    </label>
                  )}
                  <div className="inline-actions">
                    <button
                      type="button"
                      className="button secondary"
                      onClick={test}
                      disabled={busy === "test"}
                    >
                      {busy === "test" ? "测试中…" : "测试当前配置连接"}
                    </button>
                    <span className="test-result" role="status">
                      {testResult}
                    </span>
                  </div>
                </SettingsSection>
              )}

              {section === "modeling" && (
                <SettingsSection
                  title="建模默认值"
                  description="这里只定义默认推荐；每个 Run 的确认节点仍可修改。"
                >
                  <div className="model-checks">
                    {models.map((model) => (
                      <label key={model}>
                        <input
                          type="checkbox"
                          checked={(form.default_models || []).includes(model)}
                          onChange={(event) =>
                            change(
                              "default_models",
                              event.target.checked
                                ? [...(form.default_models || []), model]
                                : (form.default_models || []).filter(
                                    (value: string) => value !== model,
                                  ),
                            )
                          }
                        />
                        {model}
                      </label>
                    ))}
                  </div>
                  <div className="form-two">
                    <label>
                      内存预算 MB
                      <input
                        type="number"
                        min={256}
                        value={form.memory_budget_mb || 1536}
                        onChange={(event) => change("memory_budget_mb", Number(event.target.value))}
                      />
                    </label>
                    <label>
                      模型并发上限
                      <input
                        type="number"
                        min={1}
                        max={16}
                        value={form.max_parallel_models || 1}
                        onChange={(event) =>
                          change("max_parallel_models", Number(event.target.value))
                        }
                      />
                    </label>
                  </div>
                </SettingsSection>
              )}

              {section === "network" && (
                <SettingsSection
                  title="网络、更新与遥测"
                  description="Notebook 不是安全沙箱；关闭偏好不能替代操作系统隔离。"
                >
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={Boolean(form.notebook_network)}
                      onChange={(event) => change("notebook_network", event.target.checked)}
                    />
                    Notebook 网络偏好开启
                  </label>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={Boolean(form.auto_update)}
                      onChange={(event) => change("auto_update", event.target.checked)}
                    />
                    自动检查应用更新
                  </label>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={Boolean(form.telemetry)}
                      onChange={(event) => change("telemetry", event.target.checked)}
                    />
                    匿名遥测（默认关闭）
                  </label>
                  <div className="form-two">
                    <label>
                      单 Run Token 预算
                      <input
                        type="number"
                        min={0}
                        value={form.run_token_budget || 0}
                        onChange={(event) => change("run_token_budget", Number(event.target.value))}
                      />
                    </label>
                    <label>
                      月度 Token 预算
                      <input
                        type="number"
                        min={0}
                        value={form.monthly_token_budget || 0}
                        onChange={(event) =>
                          change("monthly_token_budget", Number(event.target.value))
                        }
                      />
                    </label>
                  </div>
                  <label>
                    代理（可选）
                    <input
                      value={form.proxy || ""}
                      onChange={(event) => change("proxy", event.target.value)}
                    />
                  </label>
                  <label>
                    自定义 CA 证书路径（可选）
                    <input
                      value={form.ca_cert || ""}
                      onChange={(event) => change("ca_cert", event.target.value)}
                    />
                  </label>
                </SettingsSection>
              )}

              {section === "workspace" && workspace && (
                <SettingsSection
                  title="工作文件夹"
                  description="首次启动选择后，后续项目级数据都保存在这里。"
                >
                  <div className="workspace-setting">
                    <code>{workspace.path}</code>
                    <button
                      type="button"
                      className="button secondary compact"
                      onClick={onChangeWorkspace}
                    >
                      更换文件夹
                    </button>
                  </div>
                  <p className="workspace-setting-note">
                    项目文件夹：{workspace.project_storage}。已有项目运行时不能切换，避免把 Run 和
                    Trace 写入不同工作区。
                  </p>
                </SettingsSection>
              )}
              {section === "workspace" && !workspace && (
                <SettingsSection title="工作文件夹" description="当前还没有读取到工作文件夹状态。">
                  <p className="empty-hint">请稍后重试。</p>
                </SettingsSection>
              )}

              {section === "backup" && (
                <SettingsSection title="备份与重置" description={`数据目录：${settings.data_dir}`}>
                  {settings.synced_path_warning && (
                    <p className="inline-warning">当前目录疑似位于同步盘，请迁移到本机专属目录。</p>
                  )}
                  <div className="inline-actions">
                    <button
                      type="button"
                      className="button secondary"
                      onClick={createBackup}
                      disabled={busy === "backup"}
                    >
                      创建数据库备份
                    </button>
                    <button type="button" className="text-danger" onClick={reset}>
                      恢复默认设置
                    </button>
                  </div>
                  <div className="backup-list">
                    {backups.slice(0, 4).map((item) => (
                      <div key={item.id}>
                        <span>{new Date(item.created_at).toLocaleString()}</span>
                        <a href={`/api/v1/backups/${item.id}/download`}>下载</a>
                      </div>
                    ))}
                  </div>
                </SettingsSection>
              )}
            </div>
          </div>
          <div className="drawer-save">
            <button className="button primary" disabled={busy === "save"}>
              {busy === "save" ? "保存中…" : "保存全部设置"}
            </button>
          </div>
        </form>
      </aside>
    </>
  );
}

function SettingsSection({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="settings-section">
      <h3>{title}</h3>
      <p>{description}</p>
      <div className="form-stack compact">{children}</div>
    </section>
  );
}
