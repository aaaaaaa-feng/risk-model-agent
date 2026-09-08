import { useState } from "react";
import { reportsApi, type SavedExport } from "../api/reportsApi";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/shared/ui/dialog";
import { errorMessage } from "@/shared/lib/format";
import { readUiPreference, writeUiPreference } from "@/shared/lib/uiPreferences";

const DIRECTORY_KEY = "risk-agent-export-directory";

function lastDirectory(): string {
  try {
    return readUiPreference(DIRECTORY_KEY) || "";
  } catch {
    return "";
  }
}

export function ExportDialog({
  label,
  save,
  onClose,
  onSaved,
}: {
  label: string;
  save: (directory: string) => Promise<SavedExport>;
  onClose: () => void;
  onSaved: (result: SavedExport) => void;
}) {
  const [directory, setDirectory] = useState(lastDirectory);
  const [busy, setBusy] = useState<"" | "picker" | "saving">("");
  const [error, setError] = useState("");
  const pick = async () => {
    setBusy("picker");
    setError("");
    try {
      const result = await reportsApi.pickExportDirectory();
      if (!result.cancelled && result.path) setDirectory(result.path);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy("");
    }
  };
  const submit = async () => {
    if (!directory.trim() || busy) return;
    setBusy("saving");
    setError("");
    try {
      const result = await save(directory.trim());
      try {
        writeUiPreference(DIRECTORY_KEY, directory.trim());
      } catch {
        /* Optional preference. */
      }
      onSaved(result);
      onClose();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy("");
    }
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent className="export-dialog" aria-describedby="export-description">
        <DialogTitle>导出 {label}</DialogTitle>
        <DialogDescription id="export-description">
          选择保存文件夹。同名文件会自动编号，原有文件会保留。
        </DialogDescription>
        <label className="export-directory">
          保存到
          <Input
            value={directory}
            disabled={Boolean(busy)}
            placeholder="选择文件夹，或输入完整文件夹路径"
            onChange={(event) => setDirectory(event.target.value)}
          />
        </label>
        <Button variant="outline" disabled={Boolean(busy)} onClick={pick}>
          {busy === "picker" ? "请在系统窗口选择…" : "选择文件夹…"}
        </Button>
        {error && (
          <p className="inline-warning" role="alert">
            {error}
          </p>
        )}
        <div className="inline-actions export-dialog-actions">
          <Button variant="outline" disabled={Boolean(busy)} onClick={onClose}>
            取消
          </Button>
          <Button disabled={Boolean(busy) || !directory.trim()} onClick={submit}>
            {busy === "saving" ? "正在保存…" : "保存文件"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
