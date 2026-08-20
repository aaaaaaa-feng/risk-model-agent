import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Settings } from "../types";

interface Props {
  open: boolean;
  settings: Settings | null;
  onClose: () => void;
  onChanged: () => void;
  notify: (message: string, error?: boolean) => void;
}

const models = ["dummy", "scorecard", "regularized_logistic", "random_forest", "extra_trees", "xgboost", "lightgbm", "catboost"];
const providerPresets: Record<string, { api_format: string; base_url: string; model: string }> = {
  deepseek: { api_format: "openai", base_url: "https://api.deepseek.com", model: "deepseek-v4-flash" },
  kimi: { api_format: "openai", base_url: "https://api.moonshot.cn/v1", model: "kimi-k2.6" },
  "kimi-code": { api_format: "openai", base_url: "https://api.kimi.com/coding/v1", model: "kimi-for-coding" },
  openai: { api_format: "openai", base_url: "https://api.openai.com/v1", model: "gpt-5" },
  anthropic: { api_format: "anthropic", base_url: "https://api.anthropic.com", model: "claude-sonnet-4-5" },
};

export function SettingsDrawer({ open, settings, onClose, onChanged, notify }: Props) {
  const [form, setForm] = useState<Record<string, any>>({});
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState("");
  const [testResult, setTestResult] = useState<string>("");
  const [backups, setBackups] = useState<any[]>([]);
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => { if (settings) setForm(settings); }, [settings]);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    window.setTimeout(() => closeRef.current?.focus(), 0);
    api.get<{ backups: any[] }>("/backups").then(v => setBackups(v.backups)).catch(() => undefined);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const focusable = Array.from(drawerRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousFocusRef.current?.focus();
    };
  }, [open]);
  if (!open || !settings) return null;
  const change = (key: string, value: any) => setForm(current => ({ ...current, [key]: value }));
  const selectProvider = (provider: string) => {
    const preset = providerPresets[provider];
    setForm(current => ({ ...current, provider, ...(preset || {}) }));
    setTestResult("");
  };
  const save = async (event: FormEvent) => {
    event.preventDefault(); setBusy("save");
    try {
      await api.put("/providers/settings", { ...form, api_key: apiKey || undefined, clear_api_key: clearKey });
      setApiKey(""); setClearKey(false); notify("设置已保存"); onChanged();
    } catch (error) { notify(error instanceof Error ? error.message : "保存失败", true); }
    finally { setBusy(""); }
  };
  const test = async () => {
    setBusy("test"); setTestResult("正在测试…");
    try {
      const result = await api.post<any>("/providers/test", { ...form, api_key: apiKey || undefined });
      setTestResult(result.ok ? `连接成功 · ${result.model}` : `连接失败 · ${result.error_code}`);
    } catch (error) { setTestResult(error instanceof Error ? error.message : "连接失败"); }
    finally { setBusy(""); }
  };
  const createBackup = async () => {
    setBusy("backup");
    try { await api.post("/backups"); const result = await api.get<{ backups: any[] }>("/backups"); setBackups(result.backups); notify("本地数据库备份已创建"); }
    catch (error) { notify(error instanceof Error ? error.message : "备份失败", true); }
    finally { setBusy(""); }
  };
  const reset = async () => {
    if (!window.confirm("恢复默认设置？项目和数据不会被删除，API Key 默认保留。")) return;
    await api.post("/system/reset-settings", { confirm: true, clear_api_key: false });
    notify("已恢复默认设置"); onChanged();
  };
  return <><div className="drawer-scrim" onMouseDown={onClose} /><aside ref={drawerRef} className="settings-drawer open" role="dialog" aria-modal="true" aria-labelledby="settings-title">
    <div className="drawer-head"><div><span className="eyebrow">SETTINGS</span><h2 id="settings-title">设置中心</h2></div><button ref={closeRef} className="icon-button inverse" onClick={onClose} aria-label="关闭设置">×</button></div>
    <form onSubmit={save} className="settings-body">
      <SettingsSection title="Provider 与密钥" description="只有 SafeEvidence 可以发送到外部 API。">
        <label>Provider<select value={form.provider || "deepseek"} onChange={e => selectProvider(e.target.value)}><option value="deepseek">DeepSeek</option><option value="kimi">Kimi 开放平台</option><option value="kimi-code">Kimi Code</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="custom">自定义</option></select></label>
        <div className="form-two"><label>API 格式<select value={form.api_format || "openai"} onChange={e => change("api_format", e.target.value)}><option value="openai">OpenAI Chat Completions</option><option value="anthropic">Anthropic Messages</option></select></label><label>启用 LLM<span className="switch-row"><input type="checkbox" checked={Boolean(form.llm_enabled)} onChange={e => change("llm_enabled", e.target.checked)} />允许聚合证据出站</span></label></div>
        <label>Base URL<input value={form.base_url || ""} onChange={e => change("base_url", e.target.value)} /></label>
        <div className="form-two"><label>主模型<input value={form.model || ""} onChange={e => change("model", e.target.value)} /></label><label>Reviewer 模型<input value={form.reviewer_model || ""} onChange={e => change("reviewer_model", e.target.value)} placeholder="留空则同主模型" /></label></div>
        <label>API Key<input type="password" autoComplete="new-password" value={apiKey} onChange={e => { setApiKey(e.target.value); if (e.target.value) setClearKey(false); }} disabled={settings.secret_storage === "environment"} placeholder={settings.api_key_configured ? "已配置；留空表示保持不变" : "输入密钥"} /></label>
        {settings.secret_storage === "environment" ? <p className="inline-warning">密钥来自环境变量，请在系统环境中移除；页面不能伪装成已清除。</p> : <label className="check-row"><input type="checkbox" checked={clearKey} onChange={e => { setClearKey(e.target.checked); if (e.target.checked) setApiKey(""); }} />保存时清除本地 API Key</label>}
        <div className="inline-actions"><button type="button" className="button secondary" onClick={test} disabled={busy === "test"}>{busy === "test" ? "测试中…" : "测试当前表单连接"}</button><span className="test-result" role="status">{testResult}</span></div>
      </SettingsSection>
      <SettingsSection title="默认建模" description="这里只定义默认推荐；每个 Run 的确认节点仍可修改。">
        <div className="model-checks">{models.map(model => <label key={model}><input type="checkbox" checked={(form.default_models || []).includes(model)} onChange={e => change("default_models", e.target.checked ? [...(form.default_models || []), model] : (form.default_models || []).filter((v: string) => v !== model))} />{model}</label>)}</div>
        <div className="form-two"><label>内存预算 MB<input type="number" min={256} value={form.memory_budget_mb || 1536} onChange={e => change("memory_budget_mb", Number(e.target.value))} /></label><label>模型并发上限<input type="number" min={1} max={16} value={form.max_parallel_models || 1} onChange={e => change("max_parallel_models", Number(e.target.value))} /></label></div>
      </SettingsSection>
      <SettingsSection title="网络、更新与遥测" description="Notebook 不是安全沙箱；关闭偏好不能替代操作系统隔离。">
        <label className="check-row"><input type="checkbox" checked={Boolean(form.notebook_network)} onChange={e => change("notebook_network", e.target.checked)} />Notebook 网络偏好开启</label>
        <label className="check-row"><input type="checkbox" checked={Boolean(form.auto_update)} onChange={e => change("auto_update", e.target.checked)} />自动检查应用更新</label>
        <label className="check-row"><input type="checkbox" checked={Boolean(form.telemetry)} onChange={e => change("telemetry", e.target.checked)} />匿名遥测（默认关闭）</label>
        <div className="form-two"><label>单 Run Token 预算<input type="number" min={0} value={form.run_token_budget || 0} onChange={e => change("run_token_budget", Number(e.target.value))} /></label><label>月度 Token 预算<input type="number" min={0} value={form.monthly_token_budget || 0} onChange={e => change("monthly_token_budget", Number(e.target.value))} /></label></div>
        <label>代理（可选）<input value={form.proxy || ""} onChange={e => change("proxy", e.target.value)} /></label><label>自定义 CA 证书路径（可选）<input value={form.ca_cert || ""} onChange={e => change("ca_cert", e.target.value)} /></label>
      </SettingsSection>
      <SettingsSection title="备份与重置" description={`数据目录：${settings.data_dir}`}>
        {settings.synced_path_warning && <p className="inline-warning">当前目录疑似位于同步盘，请迁移到本机专属目录。</p>}
        <div className="inline-actions"><button type="button" className="button secondary" onClick={createBackup} disabled={busy === "backup"}>创建数据库备份</button><button type="button" className="text-danger" onClick={reset}>恢复默认设置</button></div>
        <div className="backup-list">{backups.slice(0, 4).map(item => <div key={item.id}><span>{new Date(item.created_at).toLocaleString()}</span><a href={`/api/v1/backups/${item.id}/download`}>下载</a></div>)}</div>
      </SettingsSection>
      <div className="drawer-save"><button className="button primary" disabled={busy === "save"}>{busy === "save" ? "保存中…" : "保存全部设置"}</button></div>
    </form>
  </aside></>;
}

function SettingsSection({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <section className="settings-section"><h3>{title}</h3><p>{description}</p><div className="form-stack compact">{children}</div></section>;
}
