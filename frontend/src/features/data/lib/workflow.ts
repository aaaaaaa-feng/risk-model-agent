import type { DatasetVersion, TargetTask } from "../types";

export function initialDatasetId(datasets: DatasetVersion[], tasks: TargetTask[]): string {
  const queued = tasks.find(
    (task) =>
      task.status === "queued" && datasets.some((data) => data.id === task.dataset_version_id),
  );
  return queued?.dataset_version_id || datasets[0]?.id || "";
}

export function runnableSelection(ids: string[], tasks: TargetTask[], datasetId: string): string[] {
  return [...new Set(ids)].filter((id) =>
    tasks.some(
      (task) =>
        task.id === id &&
        task.dataset_version_id === datasetId &&
        ["queued", "failed", "blocked"].includes(task.status),
    ),
  );
}
