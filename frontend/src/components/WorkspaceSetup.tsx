import { useEffect, useState } from "react";
import { api } from "../api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { WorkspaceStatus } from "../types";

interface Props {
  workspace: WorkspaceStatus;
  onSelected: (workspace: WorkspaceStatus) => void;
  onClose?: () => void;
  notify: (message: string, error?: boolean) => void;
}

export function WorkspaceSetup({ workspace, onSelected, onClose, notify }: Props) {
  const [path, setPath] = useState(workspace.current_path || workspace.path || "");
  const [busy, setBusy] = useState("");

  useEffect(() => {
    setPath(workspace.current_path || workspace.path || "");
  }, [workspace.current_path, workspace.path]);

  const pick = async () => {
    setBusy("pick");
    try {
      const result = await api.post<{ path: string | null; cancelled: boolean }>(
        "/workspace/native-picker",
        {},
      );
      if (result.path) setPath(result.path);
    } catch (error) {
      notify(
        error instanceof Error
          ? `${error.message}；也可以直接输入路径`
          : "系统选择器不可用，请直接输入路径",
        true,
      );
    } finally {
      setBusy("");
    }
  };

  const save = async () => {
    if (!path.trim()) {
      notify("请先选择或输入工作文件夹", true);
      return;
    }
    setBusy("save");
    try {
      const result = await api.post<{ workspace: WorkspaceStatus }>("/workspace/select", {
        path: path.trim(),
      });
      onSelected(result.workspace);
    } catch (error) {
      notify(error instanceof Error ? error.message : "工作文件夹设置失败", true);
    } finally {
      setBusy("");
    }
  };

  const mandatory = workspace.needs_setup && !workspace.project_count;
  return (
    <Dialog open>
      <DialogContent
        className="workspace-setup z-[calc(var(--z-workspace-setup)+1)]"
        overlayClassName="z-[var(--z-workspace-setup)] bg-[var(--scrim-strong)] backdrop-blur-none"
        /* 工作区未选定前不允许 Escape / 点击遮罩关闭,只能走页面内按钮 */
        onEscapeKeyDown={(event) => event.preventDefault()}
        onPointerDownOutside={(event) => event.preventDefault()}
      >
        <div className="workspace-setup-head">
          <DialogTitle id="workspace-setup-title">先选择工作文件夹</DialogTitle>
        </div>
        <div className="workspace-setup-body">
          <p>
            这是本机的长期工作区。数据库、设置、Notebook、报告、模型包和评测产物都会留在这里；每个项目使用独立的{" "}
            <code>projects/&lt;项目 ID&gt;</code> 文件夹。
          </p>
          {workspace.needs_setup && workspace.project_count > 0 && (
            <p className="inline-warning">
              当前默认目录已有 {workspace.project_count.toLocaleString()}{" "}
              个历史项目。首次选择新工作区不会删除或偷偷搬迁它们；这些旧项目会保留在原目录，新建项目从现在起写入你选择的文件夹。
            </p>
          )}
          <label>
            工作文件夹路径
            <Input
              value={path}
              onChange={(event) => setPath(event.target.value)}
              placeholder="例如：D:\\RiskModelAgent 或 /Users/你的名字/RiskModelAgent"
            />
          </label>
          <div className="workspace-setup-actions">
            <Button type="button" variant="outline" onClick={pick} disabled={busy !== ""}>
              {busy === "pick" ? "正在打开…" : "打开系统选择器"}
            </Button>
            <Button type="button" onClick={save} disabled={busy !== ""}>
              {busy === "save" ? "保存中…" : "使用这个文件夹"}
            </Button>
          </div>
          {workspace.synced_path_warning && (
            <p className="inline-warning">
              当前路径疑似同步盘。风控原始数据建议放在本机专属目录，避免同步软件复制数据。
            </p>
          )}
          <p className="workspace-setup-note">
            应用只在系统应用目录保留一个工作区指针，用于下次启动找到这里；项目数据本身不会上传到云端。
          </p>
          {!mandatory && onClose && (
            <Button type="button" variant="link" className="workspace-later" onClick={onClose}>
              暂不更换，继续使用当前目录
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
