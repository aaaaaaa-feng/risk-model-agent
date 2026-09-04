import { useCallback, useEffect, useMemo, useState } from "react";
import {
  projectsApi,
  useProjectData,
  useProjects,
  type ProjectCreatedResponse,
} from "@/features/projects";
import {
  runsApi,
  shouldUseRunFallbackPolling,
  useRunData,
  type RunCreatedResponse,
} from "@/features/runs";
import { errorMessage } from "@/shared/lib/format";
import { notify } from "@/shared/lib/notify";
import { useGlobalPolling } from "./useGlobalPolling";
import { alignProjectSessionResources } from "./projectSessionState";
import { useSelectionState } from "./useSelectionState";

export interface NewProjectPayload {
  name: string;
  description: string;
  mode: string;
  metadata: Record<string, string>;
}

/**
 * 项目工作会话的唯一编排边界。
 *
 * 它组合项目列表、当前项目、当前 Run、SSE 与 URL 选择状态，但不管理主题、
 * 面板尺寸、设置抽屉等纯界面状态，避免把 AppShell 的布局职责搬成另一个上帝 Hook。
 */
export function useProjectSession() {
  const { projects, loadProjects, loadState: projectsLoadState, resetProjects } = useProjects();
  const selection = useSelectionState(projects, projectsLoadState === "loaded");
  const {
    selectedId,
    runId,
    view,
    dataMode,
    selectedRef,
    runRef,
    setRunId,
    setView,
    showDataWorkbench,
    selectProject: navigateProject,
    selectRun: navigateRun,
    openProjectData,
    resetSelection,
  } = selection;
  const { detail, loadDetail, detailAbort, clearDetail } = useProjectData(
    selectedId,
    selectedRef,
    setRunId,
  );
  const {
    run: loadedRun,
    decision: loadedDecision,
    events: loadedEvents,
    loadRun,
    runAbort,
    streamStatus,
    clearRun,
  } = useRunData(runId, selectedId, runRef, selectedRef, loadDetail);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    void loadDetail();
    const controller = detailAbort.current;
    return () => controller?.abort();
  }, [detailAbort, loadDetail]);

  useEffect(() => {
    void loadRun();
    const controller = runAbort.current;
    return () => controller?.abort();
  }, [loadRun, runAbort]);

  const current = useMemo(
    () =>
      alignProjectSessionResources(selectedId, runId, {
        detail,
        run: loadedRun,
        decision: loadedDecision,
        events: loadedEvents,
      }),
    [detail, loadedDecision, loadedEvents, loadedRun, runId, selectedId],
  );

  useGlobalPolling(loadDetail, loadRun, runId, {
    enabled: shouldUseRunFallbackPolling(streamStatus, current.run?.status),
  });

  const selectedProject = useMemo(
    () => current.detail?.project || projects.find((item) => item.id === selectedId) || null,
    [current.detail, projects, selectedId],
  );

  const selectProject = useCallback(
    (id: string) => {
      clearRun();
      clearDetail();
      navigateProject(id);
    },
    [clearDetail, clearRun, navigateProject],
  );

  const selectRun = useCallback(
    (id: string) => {
      clearRun();
      navigateRun(id);
    },
    [clearRun, navigateRun],
  );

  const createProject = useCallback(
    async (payload: NewProjectPayload): Promise<boolean> => {
      setBusy(true);
      try {
        const value: ProjectCreatedResponse = await projectsApi.create(payload);
        await loadProjects();
        clearRun();
        clearDetail();
        openProjectData(value.project.id);
        return true;
      } catch (error) {
        notify(errorMessage(error), true);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [clearDetail, clearRun, loadProjects, openProjectData],
  );

  const createDemo = useCallback(
    async (mode: string): Promise<boolean> => {
      setBusy(true);
      try {
        const value: ProjectCreatedResponse = await projectsApi.createDemo({
          mode,
          rows: 1200,
          seed: 20260821,
        });
        await loadProjects();
        clearRun();
        clearDetail();
        openProjectData(value.project.id);
        return true;
      } catch (error) {
        notify(errorMessage(error), true);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [clearDetail, clearRun, loadProjects, openProjectData],
  );

  const retry = useCallback(async () => {
    if (!current.run || !current.detail) return;
    try {
      const value: RunCreatedResponse = await runsApi.create({
        project_id: current.detail.project.id,
        target_task_id: current.run.target_task_id,
        mode: current.detail.project.mode,
      });
      selectRun(value.run.id);
    } catch (error) {
      notify(errorMessage(error), true);
    }
  }, [current.detail, current.run, selectRun]);

  const resetForWorkspace = useCallback(() => {
    resetProjects();
    clearDetail();
    clearRun();
    resetSelection();
  }, [clearDetail, clearRun, resetProjects, resetSelection]);

  const refreshDetail = useCallback(async () => {
    await loadDetail();
  }, [loadDetail]);

  return {
    projects,
    loadProjects,
    selectedId,
    runId,
    view,
    dataMode,
    detail: current.detail,
    run: current.run,
    decision: current.decision,
    events: current.events,
    streamStatus,
    selectedProject,
    busy,
    setView,
    showDataWorkbench,
    selectProject,
    selectRun,
    createProject,
    createDemo,
    retry,
    resetForWorkspace,
    refreshDetail,
    refreshRun: loadRun,
  };
}

export type ProjectSession = ReturnType<typeof useProjectSession>;
