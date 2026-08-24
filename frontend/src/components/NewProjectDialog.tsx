import { FormEvent, useRef, useState } from "react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

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
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent
        className="modal"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          inputRef.current?.focus();
        }}
      >
        <div className="modal-head">
          <div>
            <DialogTitle id="new-project-title">创建建模项目</DialogTitle>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="text-[var(--output-text)] hover:bg-[var(--on-blue-fill)] hover:text-[var(--output-text)]"
            onClick={onClose}
            aria-label="关闭"
            title="关闭新建项目弹窗"
          >
            ×
          </Button>
        </div>
        <form onSubmit={submit} className="form-stack">
          <div className="modal-form-scroll">
            <label>
              项目名称
              <Input
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
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                placeholder="本次建模的业务背景或目标"
              />
            </label>
            <label>
              协作模式
              <Select value={mode} onValueChange={setMode}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="semi_trusted">半信任：每个关键阶段确认</SelectItem>
                  <SelectItem value="fully_trusted">完全信任：Reviewer 通过后自动继续</SelectItem>
                </SelectContent>
              </Select>
            </label>
            <label>
              机构 / 产品（可选）
              <Input
                value={organization}
                onChange={(e) => setOrganization(e.target.value)}
                placeholder="不阻碍项目创建"
              />
            </label>
            <p className="boundary-note">
              项目创建不会调用 LLM；原始数据只会写入本机应用数据目录。
            </p>
            <Button
              type="button"
              className="w-full"
              disabled={busy}
              onClick={() => onCreateDemo(mode)}
              title="使用内置合成数据创建可完整体验的示例项目"
            >
              用固定种子合成多表体验完整流程
            </Button>
          </div>
          <div className="modal-actions">
            <Button type="button" variant="outline" onClick={onClose} title="取消并关闭弹窗">
              取消
            </Button>
            <Button disabled={busy || !name.trim()} title="根据当前表单创建新的建模项目">
              {busy ? "创建中…" : "创建项目"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
