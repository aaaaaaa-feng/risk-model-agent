import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { useColumnWidth } from "./model/useColumnWidth";
import { useProjectSession, type NewProjectPayload } from "./model/useProjectSession";
import { useSidebarState } from "./model/useSidebarState";
import { useTheme } from "./model/useTheme";
import { AgentRail } from "./ui/AgentRail";
import { ProjectHeader } from "./ui/ProjectHeader";
import { ProjectWorkspace } from "./ui/ProjectWorkspace";
import { ProjectSidebar } from "@/features/projects";
import { useSettings, useWorkspace, type WorkspaceStatus } from "@/features/settings";
import { Toaster } from "sonner";

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
  const session = useProjectSession();
  const { theme, toggle: toggleTheme } = useTheme();
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  // 该回调必须保持稳定，否则工作区 Hook 会把每次渲染都当成一次初始化。
  const openWorkspaceSetup = useCallback(() => setWorkspaceOpen(true), []);
  const { workspace, setWorkspace, loadWorkspace } = useWorkspace(openWorkspaceSetup);
  const { settings, loadSettings } = useSettings();

  const [createOpen, setCreateOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarOpen, toggleSidebar] = useSidebarState();
  const sidebarWidth = useColumnWidth({
    storageKey: "risk-agent-sidebar-width",
    min: 180,
    max: 360,
    initial: 224,
  });

  useEffect(() => {
    // 项目会话在自身边界内初始化；这里仅加载应用级工作区和设置。
    loadWorkspace();
    loadSettings();
  }, [loadSettings, loadWorkspace]);

  const createProject = async (payload: NewProjectPayload) => {
    if (await session.createProject(payload)) setCreateOpen(false);
  };

  const createDemo = async (mode: string) => {
    if (await session.createDemo(mode)) setCreateOpen(false);
  };

  const workspaceChanged = async (next: WorkspaceStatus) => {
    setWorkspace(next);
    setWorkspaceOpen(false);
    session.resetForWorkspace();
    await Promise.all([session.loadProjects(), loadSettings()]);
  };

  return (
    <div className="app-shell">
      <ProjectSidebar
        projects={session.projects}
        selectedId={session.selectedId}
        settings={settings}
        open={sidebarOpen}
        onToggle={toggleSidebar}
        width={sidebarOpen ? sidebarWidth.width : undefined}
        onSelect={session.selectProject}
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
        <ProjectHeader
          session={session}
          settings={settings}
          theme={theme}
          onToggleTheme={toggleTheme}
        />
        <ProjectWorkspace session={session} onCreate={() => setCreateOpen(true)} />
      </main>
      <AgentRail session={session} settings={settings} onProviderChange={loadSettings} />
      {createOpen && (
        <Suspense fallback={null}>
          <NewProjectDialog
            open
            busy={session.busy}
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
