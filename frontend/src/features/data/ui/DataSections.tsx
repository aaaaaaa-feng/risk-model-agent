import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/shared/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui/table";
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
  onSheet: (asset: DataAsset, sheet: string) => void;
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
