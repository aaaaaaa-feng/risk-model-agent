import { FormEvent, useEffect, useRef, useState } from "react";

interface Props {
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onCreate: (value: { name: string; description: string; mode: string; metadata: Record<string, string> }) => Promise<void>;
  onCreateDemo: (mode: string) => Promise<void>;
}

export function NewProjectDialog({ open, busy, onClose, onCreate, onCreateDemo }: Props) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [mode, setMode] = useState("semi_trusted");
  const [organization, setOrganization] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusTimer = window.setTimeout(() => inputRef.current?.focus(), 0);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", handleKeyDown);
      previousFocusRef.current?.focus();
    };
  }, [open]);
  if (!open) return null;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    await onCreate({ name, description, mode, metadata: organization ? { organization } : {} });
    setName(""); setDescription(""); setOrganization("");
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialogRef} className="modal" role="dialog" aria-modal="true" aria-labelledby="new-project-title">
      <div className="modal-head"><div><span className="eyebrow">NEW PROJECT</span><h2 id="new-project-title">创建建模项目</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭">×</button></div>
      <form onSubmit={submit} className="form-stack">
        <label>项目名称<input ref={inputRef} value={name} onChange={e => setName(e.target.value)} required maxLength={120} placeholder="例如：消费信贷外部数据回溯" /></label>
        <label>项目说明（可选）<textarea value={description} onChange={e => setDescription(e.target.value)} rows={3} placeholder="本次建模的业务背景或目标" /></label>
        <label>协作模式<select value={mode} onChange={e => setMode(e.target.value)}><option value="semi_trusted">半信任：每个关键阶段确认</option><option value="fully_trusted">完全信任：Reviewer 通过后自动继续</option></select></label>
        <label>机构 / 产品（可选）<input value={organization} onChange={e => setOrganization(e.target.value)} placeholder="不阻碍项目创建" /></label>
        <p className="boundary-note">项目创建不会调用 LLM；原始数据只会写入本机应用数据目录。</p>
        <button type="button" className="button demo-button" disabled={busy} onClick={() => onCreateDemo(mode)}>用固定种子合成多表体验完整流程</button>
        <div className="modal-actions"><button type="button" className="button secondary" onClick={onClose}>取消</button><button className="button primary" disabled={busy || !name.trim()}>{busy ? "创建中…" : "创建项目"}</button></div>
      </form>
    </section>
  </div>;
}
