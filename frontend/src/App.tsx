import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useRunEvents } from "./hooks";
import { isCurrentSelection, mergeEventsForRun } from "./runState";
import { errorMessage, isAbort } from "./lib/format";
import type {
  Decision,
  EventsResponse,
  Project,
  ProjectCreatedResponse,
  ProjectDetail,
  ProjectsResponse,
  Run,
  RunCreatedResponse,
  RunEvent,
  RunResponse,
  Settings,
  SettingsResponse,
  WorkspaceStatus,
} from "./types";
import { AgentChat } from "./components/AgentChat";
import { DataWorkbench } from "./components/DataWorkbench";
import { DecisionWorkbench } from "./components/DecisionWorkbench";
import { HistoryView } from "./components/HistoryView";
import { NewProjectDialog } from "./components/NewProjectDialog";
import { ProjectSidebar } from "./components/ProjectSidebar";
import { ReportView } from "./components/ReportView";
import { RunWorkbench } from "./components/RunWorkbench";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { StageRail } from "./components/StageRail";
import { Tabs } from "./components/ui/Tabs";
import { WorkspaceSetup } from "./components/WorkspaceSetup";

type View = "workbench" | "report" | "history";

export function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(
    localStorage.getItem("risk-agent-project"),
  );
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceStatus | null>(null);
  const [view, setView] = useState<View>("workbench");
  const [dataMode, setDataMode] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ message: string; error: boolean } | null>(null);
  const selectedRef = useRef<string | null>(selectedId);
  const runRef = useRef<string | null>(runId);
  const detailAbort = useRef<AbortController | null>(null);
  const runAbort = useRef<AbortController | null>(null);
  const detailRequest = useRef(0);
  const runRequest = useRef(0);
  const notify = useCallback((message: string, error = false) => {
    setToast({ message, error });
    window.setTimeout(() => setToast(null), 3200);
  }, []);
  const loadSettings = useCallback(async () => {
    try {
      const value = await api.get<SettingsResponse>("/providers/settings");
      setSettings({
        ...value.settings,
        profiles: value.profiles || value.settings?.profiles || [],
        active_profile_id: value.active_profile_id || value.settings?.active_profile_id,
      });
    } catch (error) {
      notify(errorMessage(error), true);
    }
  }, [notify]);
  const loadWorkspace = useCallback(async () => {
    try {
      const value = await api.get<{ workspace: WorkspaceStatus }>("/workspace");
      setWorkspace(value.workspace);
      if (value.workspace.needs_setup) setWorkspaceOpen(true);
    } catch (error) {
      notify(errorMessage(error), true);
    }
  }, [notify]);
  const loadProjects = useCallback(async () => {
    try {
      const value = await api.get<ProjectsResponse>("/projects");
      setProjects(value.projects);
      setSelectedId((current) => {
        if (current && value.projects.some((p) => p.id === current)) return current;
        return value.projects[0]?.id || null;
      });
    } catch (error) {
      notify(errorMessage(error), true);
    }
  }, [notify]);
  const loadDetail = useCallback(async () => {
    const projectId = selectedId;
    const requestId = ++detailRequest.current;
    detailAbort.current?.abort();
    if (!projectId) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    detailAbort.current = controller;
    try {
      const value = await api.get<ProjectDetail>(`/projects/${projectId}`, {
        signal: controller.signal,
      });
      if (requestId !== detailRequest.current || selectedRef.current !== projectId) return;
      setDetail(value);
      setRunId((current) => {
        if (current && value.runs.some((item) => item.id === current)) return current;
        const active = value.runs.find((item) =>
          ["awaiting_decision", "running", "queued"].includes(item.status),
        );
        return active?.id || value.runs[0]?.id || null;
      });
    } catch (error) {
      if (!isAbort(error)) notify(errorMessage(error), true);
    }
  }, [selectedId, notify]);
  const loadRun = useCallback(async () => {
    const projectId = selectedId;
    const expectedRunId = runId;
    const requestId = ++runRequest.current;
    runAbort.current?.abort();
    if (!expectedRunId) {
      setRun(null);
      setDecision(null);
      setEvents([]);
      return;
    }
    const controller = new AbortController();
    runAbort.current = controller;
    try {
      const [runValue, eventValue] = await Promise.all([
        api.get<RunResponse>(`/runs/${expectedRunId}`, { signal: controller.signal }),
        api.get<EventsResponse>(`/runs/${expectedRunId}/events`, { signal: controller.signal }),
      ]);
      if (
        requestId !== runRequest.current ||
        !isCurrentSelection(projectId, expectedRunId, selectedRef.current, runRef.current)
      )
        return;
      setRun(runValue.run);
      setDecision(runValue.pending_decisions?.[0] || null);
      setEvents(mergeEventsForRun([], eventValue.events, expectedRunId));
    } catch (error) {
      if (!isAbort(error)) notify(errorMessage(error), true);
    }
  }, [selectedId, runId, notify]);
  useEffect(() => {
    loadWorkspace();
    loadProjects();
    loadSettings();
  }, [loadProjects, loadSettings, loadWorkspace]);
  useEffect(() => {
    selectedRef.current = selectedId;
    if (selectedId) {
      localStorage.setItem("risk-agent-project", selectedId);
      setDataMode(false);
    }
    loadDetail();
    return () => detailAbort.current?.abort();
  }, [selectedId, loadDetail]);
  useEffect(() => {
    runRef.current = runId;
    setRun(null);
    setDecision(null);
    setEvents([]);
    loadRun();
    return () => runAbort.current?.abort();
  }, [runId, loadRun]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      loadDetail();
      if (runId) loadRun();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadDetail, loadRun, runId]);
  useRunEvents(
    runId,
    (event) => {
      if (event.run_id !== runRef.current) return;
      setEvents((current) => mergeEventsForRun(current, [event], event.run_id));
      if (
        ["awaiting_decision", "approved", "succeeded", "failed", "blocked"].includes(event.status)
      ) {
        loadRun();
        loadDetail();
      }
    },
    () => {
      loadRun();
      loadDetail();
    },
  );
  const createProject = async (payload: {
    name: string;
    description: string;
    mode: string;
    metadata: Record<string, string>;
  }) => {
    setBusy(true);
    try {
      const value = await api.post<ProjectCreatedResponse>("/projects", payload);
      await loadProjects();
      setSelectedId(value.project.id);
      setCreateOpen(false);
      setDataMode(true);
      notify("项目已创建；可开始导入本地数据");
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };
  const createDemo = async (mode: string) => {
    setBusy(true);
    try {
      const value = await api.post<ProjectCreatedResponse>("/projects/demo", {
        mode,
        rows: 1200,
        seed: 20260821,
      });
      await loadProjects();
      setSelectedId(value.project.id);
      setCreateOpen(false);
      setDataMode(true);
      notify("合成多表项目已就绪；三个 Y 可分别排队建模");
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };
  const retry = async () => {
    if (!run || !detail) return;
    try {
      const value = await api.post<RunCreatedResponse>("/runs", {
        project_id: detail.project.id,
        target_task_id: run.target_task_id,
        mode: detail.project.mode,
      });
      runRef.current = value.run.id;
      setRunId(value.run.id);
      setView("workbench");
      notify("新 Run 已进入队列");
    } catch (error) {
      notify(errorMessage(error), true);
    }
  };
  const selectProject = (id: string) => {
    selectedRef.current = id;
    runRef.current = null;
    detailAbort.current?.abort();
    runAbort.current?.abort();
    setSelectedId(id);
    setRunId(null);
    setRun(null);
    setDecision(null);
    setEvents([]);
    setView("workbench");
  };
  const selectRun = (id: string) => {
    runRef.current = id;
    setRun(null);
    setDecision(null);
    setEvents([]);
    setRunId(id);
    setDataMode(false);
    setView("workbench");
  };
  const workspaceChanged = async (next: WorkspaceStatus) => {
    setWorkspace(next);
    setWorkspaceOpen(false);
    setSelectedId(null);
    selectedRef.current = null;
    setDetail(null);
    setRunId(null);
    setRun(null);
    setDecision(null);
    setEvents([]);
    localStorage.removeItem("risk-agent-project");
    await Promise.all([loadProjects(), loadSettings()]);
  };
  const selectedProject =
    detail?.project || projects.find((item) => item.id === selectedId) || null;
  const providerStatus = settings?.llm_enabled
    ? settings.api_key_configured
      ? "LLM 已启用"
      : "缺少密钥"
    : "本地降级";
  return (
    <div className="app-shell">
      <ProjectSidebar
        projects={projects}
        selectedId={selectedId}
        settings={settings}
        onSelect={selectProject}
        onCreate={() => setCreateOpen(true)}
        onSettings={() => setSettingsOpen(true)}
      />
      <main className="main-column">
        <header className="app-header">
          <div className="head-title">
            <span>
              {selectedProject
                ? `${selectedProject.mode === "semi_trusted" ? "半信任" : "完全信任"} · ${providerStatus}`
                : "LOCAL-FIRST"}
            </span>
            <h1>{selectedProject?.name || "风控建模 Agent"}</h1>
          </div>
          <div className="head-actions">
            <span
              className={`tag ${settings?.llm_enabled && settings?.api_key_configured ? "ok" : ""}`}
            >
              {providerStatus}
            </span>
            <span className="tag network">
              Notebook {settings?.notebook_network === false ? "关闭偏好" : "网络开启"}
            </span>
            {selectedProject && (
              <button
                className="button secondary compact"
                onClick={() => {
                  setDataMode(true);
                  setView("workbench");
                }}
              >
                数据 / 新 Y
              </button>
            )}
            {decision && view === "workbench" && !dataMode && (
              <span className="tag attention">等待你的确认</span>
            )}
          </div>
        </header>
        <Tabs
          aria-label="项目主视图"
          items={[
            { id: "workbench", label: "当前工作台" },
            { id: "report", label: "产物报告" },
            { id: "history", label: "历史 Run" },
          ]}
          value={view}
          onChange={(id) => setView(id as View)}
        />
        <section
          id="main-workspace"
          className="workspace"
          role="tabpanel"
          aria-labelledby={`tab-${view}`}
        >
          {!selectedProject && <Welcome onCreate={() => setCreateOpen(true)} />}
          {selectedProject && detail && view === "workbench" && (dataMode || !run) ? (
            <DataWorkbench
              detail={detail}
              onRefresh={async () => {
                await loadDetail();
              }}
              onRunsStarted={(id) => {
                runRef.current = id;
                setRunId(id);
                setDataMode(false);
              }}
              notify={notify}
            />
          ) : null}
          {selectedProject && view === "workbench" && !dataMode && run && decision ? (
            <DecisionWorkbench
              run={run}
              decision={decision}
              onResolved={() => {
                loadRun();
                loadDetail();
              }}
              notify={notify}
            />
          ) : null}
          {selectedProject && view === "workbench" && !dataMode && run && !decision ? (
            <RunWorkbench run={run} events={events} onRetry={retry} />
          ) : null}
          {selectedProject && view === "report" && (
            <ReportView project={selectedProject} run={run} notify={notify} />
          )}
          {selectedProject && view === "history" && (
            <HistoryView
              runs={detail?.runs || []}
              tasks={detail?.target_tasks || []}
              selectedId={runId}
              onSelect={selectRun}
            />
          )}
        </section>
        <AgentChat projectId={selectedId} notify={notify} />
      </main>
      <StageRail run={run} decision={decision} events={events} />
      <NewProjectDialog
        open={createOpen}
        busy={busy}
        onClose={() => setCreateOpen(false)}
        onCreate={createProject}
        onCreateDemo={createDemo}
      />
      <SettingsDrawer
        open={settingsOpen}
        settings={settings}
        workspace={workspace}
        onClose={() => setSettingsOpen(false)}
        onChanged={loadSettings}
        onChangeWorkspace={() => setWorkspaceOpen(true)}
        notify={notify}
      />
      {workspace && workspaceOpen && (
        <WorkspaceSetup
          workspace={workspace}
          onSelected={workspaceChanged}
          onClose={
            workspace.needs_setup && workspace.project_count === 0
              ? undefined
              : () => setWorkspaceOpen(false)
          }
          notify={notify}
        />
      )}
      {toast && (
        <div className={`toast ${toast.error ? "error" : ""}`} role="status">
          {toast.message}
        </div>
      )}
    </div>
  );
}

function Welcome({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="welcome">
      <span className="eyebrow">LOCAL RISK MODELING</span>
      <h2>从一个可追溯的建模项目开始</h2>
      <p>
        导入本地 CSV / Excel，多表关联，确认多个 0/1 Y，再由主 Agent、Reviewer 与确定性 Worker
        完成闭环。
      </p>
      <div className="welcome-grid">
        <div>
          <b>01</b>
          <strong>平台不上传原始数据</strong>
          <span>
            外部 LLM 只接收经 DLP 处理的聚合 SafeEvidence；联网 Notebook 另有明确边界提示。
          </span>
        </div>
        <div>
          <b>02</b>
          <strong>关键节点可确认</strong>
          <span>半信任模式无需阅读代码，只确认业务选择。</span>
        </div>
        <div>
          <b>03</b>
          <strong>产物可独立评分</strong>
          <span>同一报告数据导出 Web、Excel、HTML 与模型包。</span>
        </div>
      </div>
      <button className="button primary" onClick={onCreate}>
        创建第一个项目
      </button>
    </div>
  );
}
