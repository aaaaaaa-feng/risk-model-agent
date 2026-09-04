import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { Moon, Sun } from "lucide-react";
import { useGlobalPolling } from "./model/useGlobalPolling";
import { useSelectionState, type View } from "./model/useSelectionState";
import { useChatRailState } from "./model/useChatRailState";
import { useColumnWidth } from "./model/useColumnWidth";
import { useSidebarState } from "./model/useSidebarState";
import { useTheme } from "./model/useTheme";
import {
  ProjectSidebar,
  projectsApi,
  useProjectData,
  useProjects,
  type ProjectCreatedResponse,
} from "@/features/projects";
import {
  runsApi,
  StagePanel,
  StageProgressBar,
  useRunData,
  type RunCreatedResponse,
} from "@/features/runs";
import { useSettings, useWorkspace, type WorkspaceStatus } from "@/features/settings";
import { errorMessage } from "@/shared/lib/format";
import { notify } from "@/shared/lib/notify";
import { removeUiPreference } from "@/shared/lib/uiPreferences";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/shared/ui/tabs";
import { Toaster } from "sonner";

const AgentChat = lazy(() =>
  import("@/features/chat/ui/AgentChat").then((module) => ({ default: module.AgentChat })),
);
const DataWorkbench = lazy(() =>
  import("@/features/data/ui/DataWorkbench").then((module) => ({
    default: module.DataWorkbench,
  })),
);
const DecisionWorkbench = lazy(() =>
  import("@/features/runs/ui/DecisionWorkbench").then((module) => ({
    default: module.DecisionWorkbench,
  })),
);
const RunWorkbench = lazy(() =>
  import("@/features/runs/ui/RunWorkbench").then((module) => ({
    default: module.RunWorkbench,
  })),
);
const HistoryView = lazy(() =>
  import("@/features/runs/ui/HistoryView").then((module) => ({ default: module.HistoryView })),
);
const ReportView = lazy(() =>
  import("@/features/reports/ui/ReportView").then((module) => ({
    default: module.ReportView,
  })),
);
const NewProjectDialog = lazy(() =>
  import("@/features/projects/ui/NewProjectDialog").then((module) => ({
    default: module.NewProjectDialog,
  })),
);
const SettingsDrawer = lazy(() =>
  import("@/features/settings/ui/SettingsDrawer").then((module) => ({
    default: module.SettingsDrawer,
  })),
);
const WorkspaceSetup = lazy(() =>
  import("@/features/settings/ui/WorkspaceSetup").then((module) => ({
    default: module.WorkspaceSetup,
  })),
);

export function AppShell() {
  const { projects, loadProjects } = useProjects();
  const { theme, toggle: toggleTheme } = useTheme();
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
      const value: ProjectCreatedResponse = await projectsApi.create(payload);
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
      const value: ProjectCreatedResponse = await projectsApi.createDemo({
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
      const value: RunCreatedResponse = await runsApi.create({
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
    removeUiPreference("risk-agent-project");
    await Promise.all([loadProjects(), loadSettings()]);
  };

  const selectedProject =
    detail?.project || projects.find((item) => item.id === selectedId) || null;
  const providerStatus = settings?.llm_enabled
    ? settings.api_key_configured
      ? "LLM 已启用"
      : "API 未连接"
    : settings?.api_key_configured
      ? "LLM 已关闭"
      : "API 未连接";

  return (
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
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleTheme}
              aria-label={theme === "dark" ? "切换到白天模式" : "切换到黑夜模式"}
              title={theme === "dark" ? "切换到白天模式" : "切换到黑夜模式"}
            >
              {theme === "dark" ? <Sun /> : <Moon />}
            </Button>
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
          <Suspense fallback={<div className="loading-panel">正在加载当前视图…</div>}>
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
          </Suspense>
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
            title="展开右侧 Agent 对话栏"
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
                title="收起右侧 Agent 对话栏"
                onClick={chatRail.toggle}
              >
                ▸
              </button>
            </div>
            <Suspense fallback={<p className="chat-placeholder">正在加载 Agent 对话…</p>}>
              <AgentChat
                projectId={selectedId}
                settings={settings}
                onProviderChange={loadSettings}
              />
            </Suspense>
          </>
        )}
      </aside>
      {createOpen && (
        <Suspense fallback={null}>
          <NewProjectDialog
            open
            busy={busy}
            onClose={() => setCreateOpen(false)}
            onCreate={createProject}
            onCreateDemo={createDemo}
          />
        </Suspense>
      )}
      {settingsOpen && (
        <Suspense fallback={null}>
          <SettingsDrawer
            open
            settings={settings}
            workspace={workspace}
            onClose={() => setSettingsOpen(false)}
            onChanged={loadSettings}
            onChangeWorkspace={() => setWorkspaceOpen(true)}
          />
        </Suspense>
      )}
      {workspace && workspaceOpen && (
        <Suspense fallback={null}>
          <WorkspaceSetup
            workspace={workspace}
            onSelected={workspaceChanged}
            onClose={
              workspace.needs_setup && workspace.project_count === 0
                ? undefined
                : () => setWorkspaceOpen(false)
            }
          />
        </Suspense>
      )}
      <Toaster
        position="top-right"
        toastOptions={{ unstyled: true, classNames: { toast: "app-toast" } }}
      />
    </div>
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
