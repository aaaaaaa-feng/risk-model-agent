import { useCallback, useEffect, useRef, useState } from "react";
import type { Project } from "@/features/projects";
import {
  readUiPreference,
  removeUiPreference,
  writeUiPreference,
} from "@/shared/lib/uiPreferences";
import {
  isView,
  parseSelectionHash,
  serializeSelectionHash,
  type SelectionRoute,
  type View,
} from "./selectionRoute";

export type { View } from "./selectionRoute";

function initialSelection(): SelectionRoute {
  if (typeof window !== "undefined") {
    const route = parseSelectionHash(window.location.hash);
    if (route) return route;
  }
  const savedView = readUiPreference("risk-agent-view");
  return {
    projectId: readUiPreference("risk-agent-project"),
    runId: readUiPreference("risk-agent-run"),
    view: isView(savedView) ? savedView : "workbench",
    dataMode: readUiPreference("risk-agent-workbench-mode") === "data",
  };
}

function writePreference(key: string, value: string | null): void {
  if (value) writeUiPreference(key, value);
  else removeUiPreference(key);
}

function replaceBrowserRoute(route: SelectionRoute): void {
  if (typeof window === "undefined") return;
  const hash = serializeSelectionHash(route);
  if (window.location.hash === hash) return;
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}${hash}`,
  );
}

function pushBrowserRoute(route: SelectionRoute): void {
  if (typeof window === "undefined") return;
  const hash = serializeSelectionHash(route);
  if (window.location.hash === hash) return;
  window.history.pushState(null, "", `${window.location.pathname}${window.location.search}${hash}`);
}

/**
 * 项目列表尚未完成首次读取时，空数组不能被解释为“工作区没有项目”。
 * 只有服务端成功返回列表后，才校正已经失效的项目/Run 路由。
 */
export function reconcileSelectionWithProjects(
  route: SelectionRoute,
  projects: Project[],
  projectsLoaded: boolean,
): SelectionRoute {
  if (
    !projectsLoaded ||
    (route.projectId && projects.some((project) => project.id === route.projectId))
  )
    return route;
  return {
    projectId: projects[0]?.id || null,
    runId: null,
    view: "workbench",
    dataMode: false,
  };
}

export function useSelectionState(projects: Project[], projectsLoaded: boolean) {
  const [initial] = useState(initialSelection);
  const [selectedId, setSelectedId] = useState<string | null>(initial.projectId);
  const [runId, setRunId] = useState<string | null>(initial.runId);
  const [view, setViewState] = useState<View>(initial.view);
  const [dataMode, setDataModeState] = useState(initial.dataMode);
  const selectedRef = useRef<string | null>(selectedId);
  const runRef = useRef<string | null>(runId);
  const projectsRef = useRef(projects);
  const projectsLoadedRef = useRef(projectsLoaded);
  const routeRef = useRef<SelectionRoute>({
    projectId: selectedId,
    runId,
    view,
    dataMode,
  });
  projectsRef.current = projects;
  projectsLoadedRef.current = projectsLoaded;

  useEffect(() => {
    const current = routeRef.current;
    const next = reconcileSelectionWithProjects(current, projects, projectsLoaded);
    if (next === current) return;
    routeRef.current = next;
    selectedRef.current = next.projectId;
    runRef.current = null;
    replaceBrowserRoute(next);
    setSelectedId(next.projectId);
    setRunId(null);
    setViewState(next.view);
    setDataModeState(next.dataMode);
  }, [projects, projectsLoaded, selectedId]);

  useEffect(() => {
    selectedRef.current = selectedId;
    writePreference("risk-agent-project", selectedId);
  }, [selectedId]);

  useEffect(() => {
    runRef.current = runId;
    writePreference("risk-agent-run", runId);
  }, [runId]);

  useEffect(() => {
    writePreference("risk-agent-view", view);
    writePreference("risk-agent-workbench-mode", dataMode ? "data" : null);
    const route = { projectId: selectedId, runId, view, dataMode };
    routeRef.current = route;
    replaceBrowserRoute(route);
  }, [dataMode, runId, selectedId, view]);

  useEffect(() => {
    const restoreRoute = () => {
      const parsed = parseSelectionHash(window.location.hash);
      if (!parsed) return;
      const route = reconcileSelectionWithProjects(
        parsed,
        projectsRef.current,
        projectsLoadedRef.current,
      );
      if (route !== parsed) replaceBrowserRoute(route);
      routeRef.current = route;
      selectedRef.current = route.projectId;
      runRef.current = route.runId;
      setSelectedId(route.projectId);
      setRunId(route.runId);
      setViewState(route.view);
      setDataModeState(route.dataMode);
    };
    window.addEventListener("popstate", restoreRoute);
    window.addEventListener("hashchange", restoreRoute);
    return () => {
      window.removeEventListener("popstate", restoreRoute);
      window.removeEventListener("hashchange", restoreRoute);
    };
  }, []);

  const navigate = useCallback((next: SelectionRoute) => {
    routeRef.current = next;
    selectedRef.current = next.projectId;
    runRef.current = next.runId;
    pushBrowserRoute(next);
    setSelectedId(next.projectId);
    setRunId(next.runId);
    setViewState(next.view);
    setDataModeState(next.dataMode);
  }, []);

  const selectProject = useCallback(
    (id: string) => {
      selectedRef.current = id;
      runRef.current = null;
      navigate({ projectId: id, runId: null, view: "workbench", dataMode: false });
    },
    [navigate],
  );

  const selectRun = useCallback(
    (id: string) => {
      runRef.current = id;
      navigate({ ...routeRef.current, runId: id, view: "workbench", dataMode: false });
    },
    [navigate],
  );

  const setView = useCallback(
    (nextView: View) => {
      navigate({ ...routeRef.current, view: nextView });
    },
    [navigate],
  );

  const setDataMode = useCallback(
    (nextDataMode: boolean) => {
      navigate({
        ...routeRef.current,
        view: nextDataMode ? "workbench" : routeRef.current.view,
        dataMode: nextDataMode,
      });
    },
    [navigate],
  );

  const showDataWorkbench = useCallback(() => {
    navigate({ ...routeRef.current, view: "workbench", dataMode: true });
  }, [navigate]);

  const openProjectData = useCallback(
    (projectId: string) => {
      navigate({ projectId, runId: null, view: "workbench", dataMode: true });
    },
    [navigate],
  );

  const resetSelection = useCallback(() => {
    const route: SelectionRoute = {
      projectId: null,
      runId: null,
      view: "workbench",
      dataMode: false,
    };
    routeRef.current = route;
    selectedRef.current = null;
    runRef.current = null;
    replaceBrowserRoute(route);
    setSelectedId(null);
    setRunId(null);
    setViewState("workbench");
    setDataModeState(false);
  }, []);

  return {
    selectedId,
    setSelectedId,
    runId,
    setRunId,
    view,
    setView,
    dataMode,
    setDataMode,
    selectedRef,
    runRef,
    selectProject,
    selectRun,
    showDataWorkbench,
    openProjectData,
    resetSelection,
  };
}
