import { FormEvent, useRef, useState } from "react";
import { Dialog } from "./ui/Dialog";

interface Props {
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onCreate: (value: {
    name: string;
    description: string;
    mode: string;
    metadata: Record<string, string>;
  }) => Promise<void>;
  onCreateDemo: (mode: string) => Promise<void>;
}

export function NewProjectDialog({ open, busy, onClose, onCreate, onCreateDemo }: Props) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [mode, setMode] = useState("semi_trusted");
  const [organization, setOrganization] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    await onCreate({ name, description, mode, metadata: organization ? { organization } : {} });
    setName("");
    setDescription("");
    setOrganization("");
  };

  return (
    <Dialog open={open} titleId="new-project-title" onClose={onClose}>
      <div className="modal-head">
        <div>
          <span className="eyebrow">NEW PROJECT</span>
          <h2 id="new-project-title">创建建模项目</h2>
        </div>
        <button className="icon-button" onClick={onClose} aria-label="关闭">
          ×
        </button>
      </div>
      <form onSubmit={submit} className="form-stack">
        <label>
          项目名称
          <input
            ref={inputRef}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={120}
            placeholder="例如：消费信贷外部数据回溯"
          />
        </label>
        <label>
          项目说明（可选）
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="本次建模的业务背景或目标"
          />
        </label>
        <label>
          协作模式
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="semi_trusted">半信任：每个关键阶段确认</option>
            <option value="fully_trusted">完全信任：Reviewer 通过后自动继续</option>
          </select>
        </label>
        <label>
          机构 / 产品（可选）
          <input
            value={organization}
            onChange={(e) => setOrganization(e.target.value)}
            placeholder="不阻碍项目创建"
          />
        </label>
        <p className="boundary-note">项目创建不会调用 LLM；原始数据只会写入本机应用数据目录。</p>
        <button
          type="button"
          className="button demo-button"
          disabled={busy}
          onClick={() => onCreateDemo(mode)}
        >
          用固定种子合成多表体验完整流程
        </button>
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>
            取消
          </button>
          <button className="button primary" disabled={busy || !name.trim()}>
            {busy ? "创建中…" : "创建项目"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
