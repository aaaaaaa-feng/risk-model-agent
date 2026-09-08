import { httpClient, eventUrl } from "@/shared/api/client";
import type { DownloadedFile } from "@/shared/api/client";
import type { ModelVersion, ReportData, ScoreJob } from "../types";

export interface SavedExport {
  path: string;
  filename: string;
  size_bytes: number;
  sha256: string;
}

export const reportsApi = {
  previewUrl: (runId: string) => eventUrl(`/reports/${encodeURIComponent(runId)}/preview`),
  pickExportDirectory: () =>
    httpClient.post<{ path: string | null; cancelled: boolean }>("/exports/native-picker"),
  exportReport: (runId: string, format: "excel" | "html" | "model", directory: string) =>
    httpClient.post<SavedExport>(`/reports/${encodeURIComponent(runId)}/export`, {
      format,
      directory,
    }),
  exportScores: (jobId: string, directory: string) =>
    httpClient.post<SavedExport>(`/score-jobs/${encodeURIComponent(jobId)}/export`, { directory }),
  models: (projectId: string, signal?: AbortSignal) =>
    httpClient.get<{ models: ModelVersion[] }>(
      `/projects/${encodeURIComponent(projectId)}/models`,
      { signal },
    ),
  report: (runId: string, signal?: AbortSignal) =>
    httpClient.get<ReportData>(`/reports/${encodeURIComponent(runId)}`, { signal }),
  uploadScoreInput: (projectId: string, form: FormData) =>
    httpClient.upload<{ asset: { id: string } }>(
      `/projects/${encodeURIComponent(projectId)}/data-assets`,
      form,
    ),
  createScoreJob: (modelVersionId: string, inputAssetId: string) =>
    httpClient.post<{ score_job: ScoreJob }>("/score-jobs", {
      model_version_id: modelVersionId,
      input_asset_id: inputAssetId,
    }),
  downloadExcel: (runId: string): Promise<DownloadedFile> =>
    httpClient.download(`/reports/${encodeURIComponent(runId)}/excel`),
  downloadHtml: (runId: string): Promise<DownloadedFile> =>
    httpClient.download(`/reports/${encodeURIComponent(runId)}/html`),
  downloadScores: (scoreJobId: string): Promise<DownloadedFile> =>
    httpClient.download(`/score-jobs/${encodeURIComponent(scoreJobId)}/download`),
};
