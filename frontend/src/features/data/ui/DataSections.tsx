import { useLayoutEffect, useRef, useState, type ChangeEvent } from "react";
import { dataApi } from "../api/dataApi";
import { errorMessage } from "@/shared/lib/format";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Hint } from "@/shared/ui/hint";
import { Input } from "@/shared/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/shared/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui/table";
import { Textarea } from "@/shared/ui/textarea";
import type { DataAsset } from "../types";

function Empty({ text }: { text: string }) {
  return (
    <div className="empty-state">
      <span>EMPTY</span>
      <p>{text}</p>
    </div>
  );
}

export function AssetTable({
  assets,
  busy,
  onSheet,
  onMaterialize,
}: {
  assets: DataAsset[];
  busy: string;
  onSheet: (a: DataAsset, s: string) => void;
  onMaterialize: (id: string) => void;
}) {
  if (!assets.length) return <Empty text="尚未导入文件。原始文件不会上传到云端。" />;
  return (
    <div className="table-wrap">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>用途</TableHead>
            <TableHead>文件</TableHead>
            <TableHead>格式</TableHead>
            <TableHead>规模</TableHead>
            <TableHead>状态 / Sheet</TableHead>
            <TableHead>操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {assets.map((asset) => (
            <TableRow key={asset.id}>
              <TableCell>{asset.kind}</TableCell>
              <TableCell>
                <strong>{asset.name}</strong>
              </TableCell>
              <TableCell>{asset.format.toUpperCase()}</TableCell>
              <TableCell>
                {asset.rows == null
                  ? "待选择"
                  : `${asset.rows.toLocaleString()} × ${asset.columns}`}
              </TableCell>
              <TableCell>
                {asset.status === "sheet_selection_required" ? (
                  <Select onValueChange={(value) => onSheet(asset, value)}>
                    <SelectTrigger className="h-[34px]">
                      <SelectValue placeholder="选择 Sheet" />
                    </SelectTrigger>
                    <SelectContent>
                      {asset.metadata?.sheets?.map((sheet) => (
                        <SelectItem key={sheet} value={sheet}>
                          {sheet}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Badge variant="ok">ready</Badge>
                )}
              </TableCell>
              <TableCell>
                <Button
                  variant="link"
                  size="sm"
                  disabled={
                    asset.status !== "ready" || busy === asset.id || asset.kind === "dictionary"
                  }
                  onClick={() => onMaterialize(asset.id)}
                >
                  {busy === asset.id ? "生成中…" : "生成数据版本"}
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

interface NotebookEditorProps {
  notebook: { id: string; name: string; dataset_version_id?: string };
  document: { cells: NotebookCell[] };
  setDocument: (value: { cells: NotebookCell[] }) => void;
  onRefresh: () => Promise<void>;
  notify: (message: string, error?: boolean) => void;
}

export interface NotebookCell {
  cell_type: string;
  source: string;
  execution_count?: number;
  outputs?: NotebookOutput[];
}

interface NotebookOutput {
  text?: string;
  ename?: string;
  evalue?: string;
  data?: Record<string, unknown>;
}

interface NotebookExecuteResponse {
  execution: {
    status: string;
    execution_count?: number;
    outputs?: NotebookOutput[];
  };
}

export function NotebookEditor({
  notebook,
  document,
  setDocument,
  onRefresh,
  notify,
}: NotebookEditorProps) {
  const [busy, setBusy] = useState("");
  const [output, setOutput] = useState("joined_output.csv");
  const [label, setLabel] = useState("Notebook 关联结果");
  const save = async () => {
    setBusy("save");
    try {
      await dataApi.saveNotebook(notebook.id, document);
    } catch (e) {
      notify(errorMessage(e), true);
    } finally {
      setBusy("");
    }
  };
  const execute = async (index: number) => {
    setBusy(`cell-${index}`);
    try {
      await save();
      const result: NotebookExecuteResponse = await dataApi.executeNotebookCell(notebook.id, index);
      const copy = structuredClone(document);
      copy.cells[index].outputs = result.execution.outputs;
      copy.cells[index].execution_count = result.execution.execution_count;
      setDocument(copy);
      if (result.execution.status !== "succeeded") {
        notify(
          errorMessage({ code: "NOTEBOOK_CELL_EXECUTION_FAILED" }, { context: "notebook" }),
          true,
        );
      }
    } catch (e) {
      notify(errorMessage(e), true);
    } finally {
      setBusy("");
    }
  };
  const importOutput = async () => {
    setBusy("import");
    try {
      await dataApi.importNotebookOutput(notebook.id, {
        relative_path: output,
        label,
        parent_dataset_version_id: notebook.dataset_version_id || null,
        expected_grain: "same_or_fewer_rows",
      });
      await onRefresh();
    } catch (e) {
      notify(errorMessage(e), true);
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="notebook-editor">
      <div className="section-heading">
        <div>
          <h3>
            {notebook.name}
            <Hint text="网络默认开启 · 用户代码不在安全沙箱中 · 产品不会主动外发原始数据" />
          </h3>
        </div>
        <Button variant="outline" onClick={save}>
          {busy === "save" ? "保存中…" : "保存 Notebook"}
        </Button>
      </div>
      {document.cells.map((cell, index) => (
        <div className={`nb-cell ${cell.cell_type}`} key={index}>
          <div className="nb-gutter">[{cell.execution_count ?? " "}]</div>
          {cell.cell_type === "code" ? (
            <>
              <NotebookSource
                value={cell.source}
                ariaLabel={`第 ${index + 1} 个代码单元格`}
                onChange={(e) => {
                  const copy = structuredClone(document);
                  copy.cells[index].source = e.target.value;
                  setDocument(copy);
                }}
                spellCheck={false}
              />
              <button
                className="cell-run"
                onClick={() => execute(index)}
                disabled={Boolean(busy)}
                title={`运行第 ${index + 1} 个 Notebook 单元格`}
              >
                ▶ 运行
              </button>
              {cell.outputs && cell.outputs.length > 0 && (
                <pre className="cell-output">
                  {cell.outputs
                    .map(
                      (item) =>
                        (item as NotebookOutput).text ||
                        ((item as NotebookOutput).evalue || (item as NotebookOutput).ename
                          ? errorMessage(
                              {
                                code: (item as NotebookOutput).ename,
                                message: (item as NotebookOutput).evalue,
                              },
                              { context: "notebook" },
                            )
                          : "") ||
                        JSON.stringify((item as NotebookOutput).data || {}),
                    )
                    .join("\n")}
                </pre>
              )}
            </>
          ) : (
            <NotebookSource
              value={cell.source}
              ariaLabel={`第 ${index + 1} 个 Markdown 单元格`}
              onChange={(e) => {
                const copy = structuredClone(document);
                copy.cells[index].source = e.target.value;
                setDocument(copy);
              }}
            />
          )}
        </div>
      ))}
      <div className="import-output">
        <label>
          输出文件
          <Input value={output} onChange={(e) => setOutput(e.target.value)} />
        </label>
        <label>
          数据版本名称
          <Input value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <Button onClick={importOutput} disabled={busy === "import"}>
          {busy === "import" ? "校验中…" : "校验并生成数据版本"}
        </Button>
      </div>
    </div>
  );
}

function NotebookSource({
  value,
  ariaLabel,
  onChange,
  spellCheck,
}: {
  value: string;
  ariaLabel: string;
  onChange: (event: ChangeEvent<HTMLTextAreaElement>) => void;
  spellCheck?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;

    const resize = () => {
      element.style.height = "auto";
      element.style.height = `${element.scrollHeight + 2}px`;
    };
    resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [value]);

  return (
    <Textarea
      ref={ref}
      className="nb-source"
      rows={1}
      aria-label={ariaLabel}
      value={value}
      onChange={onChange}
      spellCheck={spellCheck}
    />
  );
}
