import { describe, expect, it } from "vitest";
import { initialDatasetId, runnableSelection } from "@/features/data/lib/workflow";
import type { DatasetVersion, TargetTask } from "@/features/data";

const datasets = [{ id: "new-cleaned" }, { id: "prepared" }] as DatasetVersion[];
const tasks = [
  { id: "one", dataset_version_id: "prepared", status: "queued" },
  { id: "old", dataset_version_id: "new-cleaned", status: "queued" },
  { id: "busy", dataset_version_id: "prepared", status: "running" },
  { id: "done", dataset_version_id: "prepared", status: "succeeded" },
] as TargetTask[];
describe("数据准备的连续操作", () => {
  it("重新进入时优先使用待启动任务的数据，不误选训练衍生的新版本", () => {
    expect(initialDatasetId(datasets, tasks)).toBe("prepared");
  });
  it("当前数据版本只能启动所选的可运行任务，不混入旧版本和已启动任务", () => {
    expect(runnableSelection(["one", "old", "busy", "done", "one"], tasks, "prepared")).toEqual([
      "one",
    ]);
  });
  it("用户取消全部勾选后不会自动重新选择", () => {
    expect(runnableSelection([], tasks, "prepared")).toEqual([]);
  });
  it("刚生成的新数据版本没有旧任务被隐式带入", () => {
    expect(runnableSelection(["one"], tasks, "just-created")).toEqual([]);
  });
});
