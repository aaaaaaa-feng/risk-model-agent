import { useEffect, useState } from "react";
import { api } from "./api";
import { useGlobalPolling } from "./hooks/useGlobalPolling";
import { useProjectData } from "./hooks/useProjectData";
import { useProjects } from "./hooks/useProjects";
import { useRunData } from "./hooks/useRunData";
import { useSelectionState, type View } from "./hooks/useSelectionState";
import { useSettings } from "./hooks/useSettings";
import { useToast } from "./hooks/useToast";
import { useWorkspace } from "./hooks/useWorkspace";
import { errorMessage } from "./lib/format";
import type { ProjectCreatedResponse, RunCreatedResponse, WorkspaceStatus } from "./types";
import { AppStateContext } from "./stores/AppStateContext";
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

export function App() {
  const { toast, notify } = useToast();
  const { projects, loadProjects } = useProjects(notify);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const { workspace, setWorkspace, loadWorkspace } = useWorkspace(notify, () =>
    setWorkspaceOpen(true),
  );
  const { settings, loadSettings } = useSettings(notify);
  const {
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
    selectProject: baseSelectProject,
    selectRun: baseSelectRun,
  } = useSelectionState(projects);
  const { detail, loadDetail, detailAbort, clearDetail } = useProjectData(
    selectedId,
    selectedRef,
    setRunId,
    notify,
  );
  const { run, setRun, decision, setDecision, events, setEvents, loadRun, runAbort, clearRun } =
    useRunData(runId, selectedId, runRef, selectedRef, loadDetail, notify);

  const [createOpen, setCreateOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    loadWorkspace();
    loadProjects();
    loadSettings();
  }, [loadProjects, loadSettings, loadWorkspace]);

  useEffect(() => {
    loadDetail();
    const controller = detailAbort.current;
    return () => controller?.abort();
  }, [loadDetail, detailAbort]);

  useEffect(() => {
    loadRun();
    const controller = runAbort.current;
    return () => controller?.abort();
  }, [loadRun, runAbort]);

  useGlobalPolling(loadDetail, loadRun, runId);

  const selectProject = (id: string) => {
    clearRun();
    clearDetail();
    baseSelectProject(id);
  };

  const selectRun = (id: string) => {
    setRun(null);
    setDecision(null);
    setEvents([]);
    baseSelectRun(id);
  };

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

  const workspaceChanged = async (next: WorkspaceStatus) => {
    setWorkspace(next);
    setWorkspaceOpen(false);
    setSelectedId(null);
    selectedRef.current = null;
    clearDetail();
    setRunId(null);
    clearRun();
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
    <AppStateContext.Provider
      value={{
        notify,
        settings,
        workspace,
        projects,
        selectedId,
        detail,
        run,
        decision,
      }}
    >
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
                onRefresh={loadDetail}
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
    </AppStateContext.Provider>
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
