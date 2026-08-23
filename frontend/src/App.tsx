import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { api } from "./api";
import { useGlobalPolling } from "./hooks/useGlobalPolling";
import { useProjectData } from "./hooks/useProjectData";
import { useProjects } from "./hooks/useProjects";
import { useRunData } from "./hooks/useRunData";
import { useSelectionState, type View } from "./hooks/useSelectionState";
import { useSettings } from "./hooks/useSettings";
import { useChatRailState } from "./hooks/useChatRailState";
import { useColumnWidth } from "./hooks/useColumnWidth";
import { useSidebarState } from "./hooks/useSidebarState";
import { useWorkspace } from "./hooks/useWorkspace";
import { errorMessage } from "./lib/format";
import { notify } from "@/lib/notify";
import type { ProjectCreatedResponse, RunCreatedResponse, WorkspaceStatus } from "./types";
import { AppStateContext } from "./stores/AppStateContext";
import { AgentChat } from "./components/AgentChat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataWorkbench } from "./components/DataWorkbench";
import { DecisionWorkbench } from "./components/DecisionWorkbench";
import { HistoryView } from "./components/HistoryView";
import { NewProjectDialog } from "./components/NewProjectDialog";
import { ProjectSidebar } from "./components/ProjectSidebar";
import { ReportView } from "./components/ReportView";
import { RunWorkbench } from "./components/RunWorkbench";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { StagePanel } from "./components/StagePanel";
import { StageProgressBar } from "./components/StageProgressBar";
import { Tabs, TabsList, TabsTrigger } from "./components/ui/tabs";
import { Toaster } from "sonner";
import { WorkspaceSetup } from "./components/WorkspaceSetup";

export function App() {
  const { projects, loadProjects } = useProjects();
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  // 该回调必须保持稳定，否则工作区 Hook 会把每次渲染都当成一次初始化。
  const openWorkspaceSetup = useCallback(() => setWorkspaceOpen(true), []);
  const { workspace, setWorkspace, loadWorkspace } = useWorkspace(openWorkspaceSetup);
  const { settings, loadSettings } = useSettings();
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
  );
  const { run, setRun, decision, setDecision, events, setEvents, loadRun, runAbort, clearRun } =
    useRunData(runId, selectedId, runRef, selectedRef, loadDetail);

  const [createOpen, setCreateOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sidebarOpen, toggleSidebar] = useSidebarState();
  const chatRail = useChatRailState();
  const sidebarWidth = useColumnWidth({
    storageKey: "risk-agent-sidebar-width",
    min: 180,
    max: 360,
    initial: 224,
  });
  const chatWidth = useColumnWidth({
    storageKey: "risk-agent-chat-rail-width",
    min: 240,
    max: 520,
    initial: typeof window !== "undefined" && window.innerWidth < 1440 ? 260 : 320,
    invert: true,
  });

  useEffect(() => {
    // 应用启动时只加载一次基础上下文；项目详情和 Run 事件由各自的 Hook 管理。
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
          open={sidebarOpen}
          onToggle={toggleSidebar}
          width={sidebarOpen ? sidebarWidth.width : undefined}
          onSelect={selectProject}
          onCreate={() => setCreateOpen(true)}
          onSettings={() => setSettingsOpen(true)}
        />
        {sidebarOpen && (
          <div
            className="col-resizer"
            aria-label="调整项目列表宽度"
            title="拖动调整宽度，双击恢复默认"
            {...sidebarWidth.resizerProps}
          />
        )}
        <main className="main-column">
          <StageProgressBar run={run} />
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
              <Badge
                variant={settings?.llm_enabled && settings?.api_key_configured ? "ok" : "neutral"}
                className="max-[1100px]:hidden"
              >
                {providerStatus}
              </Badge>
              <Badge variant="network" className="max-[1350px]:hidden">
                Notebook {settings?.notebook_network === false ? "关闭偏好" : "网络开启"}
              </Badge>
              {selectedProject && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setDataMode(true);
                    setView("workbench");
                  }}
                >
                  数据 / 新 Y
                </Button>
              )}
              {decision && view === "workbench" && !dataMode && (
                <Badge variant="attention" className="max-[1100px]:hidden">
                  等待你的确认
                </Badge>
              )}
            </div>
          </header>
          <Tabs value={view} onValueChange={(id) => setView(id as View)} className="contents">
            <TabsList className="primary-tabs" aria-label="项目主视图">
              <TabsTrigger value="workbench" id="tab-workbench">
                当前工作台
              </TabsTrigger>
              <TabsTrigger value="report" id="tab-report">
                产物报告
              </TabsTrigger>
              <TabsTrigger value="history" id="tab-history">
                历史 Run
              </TabsTrigger>
            </TabsList>
          </Tabs>
          <StagePanel run={run} decision={decision} events={events} />
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
              />
            ) : null}
            {selectedProject && view === "workbench" && !dataMode && run && !decision ? (
              <RunWorkbench run={run} events={events} onRetry={retry} />
            ) : null}
            {selectedProject && view === "report" && (
              <ReportView project={selectedProject} run={run} />
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
        </main>
        {!chatRail.collapsed && (
          <div
            className="col-resizer"
            aria-label="调整对话栏宽度"
            title="拖动调整宽度，双击恢复默认"
            {...chatWidth.resizerProps}
          />
        )}
        <aside
          className={`chat-rail ${chatRail.collapsed ? "collapsed" : chatRail.mode}`}
          style={
            chatRail.collapsed
              ? undefined
              : ({ "--chat-rail-width": `${chatWidth.width}px` } as CSSProperties)
          }
          aria-label="Agent 对话"
        >
          {chatRail.collapsed ? (
            <button
              className="chat-expand"
              type="button"
              aria-expanded={false}
              aria-label="展开 Agent 对话栏"
              onClick={chatRail.toggle}
            >
              ◂
            </button>
          ) : (
            <>
              <div className="chat-rail-head">
                <span>AGENT</span>
                <button
                  className="chat-collapse"
                  type="button"
                  aria-expanded={true}
                  aria-label="收起 Agent 对话栏"
                  onClick={chatRail.toggle}
                >
                  ▸
                </button>
              </div>
              <AgentChat projectId={selectedId} />
            </>
          )}
        </aside>
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
          />
        )}
        <Toaster
          position="top-right"
          toastOptions={{ unstyled: true, classNames: { toast: "app-toast" } }}
        />
      </div>
    </AppStateContext.Provider>
  );
}

function Welcome({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="welcome">
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
      <Button className="h-[38px] px-[18px]" onClick={onCreate}>
        创建第一个项目
      </Button>
    </div>
  );
}
