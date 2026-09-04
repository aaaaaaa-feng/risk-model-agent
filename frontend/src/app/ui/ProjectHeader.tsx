import { Moon, Sun } from "lucide-react";
import type { ProjectSession } from "../model/useProjectSession";
import type { View } from "../model/selectionRoute";
import { StagePanel, StageProgressBar } from "@/features/runs";
import type { Settings } from "@/features/settings";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/shared/ui/tabs";

interface Props {
  session: ProjectSession;
  settings: Settings | null;
  theme: "light" | "dark";
  onToggleTheme: () => void;
}

function providerStatus(settings: Settings | null): string {
  if (!settings?.api_key_configured) return "API 未连接";
  return settings.llm_enabled ? "LLM 已启用" : "LLM 已关闭";
}

export function ProjectHeader({ session, settings, theme, onToggleTheme }: Props) {
  const status = providerStatus(settings);
  return (
    <>
      <StageProgressBar run={session.run} />
      <header className="app-header">
        <div className="head-title">
          <span>
            {session.selectedProject
              ? `${session.selectedProject.mode === "semi_trusted" ? "半信任" : "完全信任"} · ${status}`
              : "LOCAL-FIRST"}
          </span>
          <h1>{session.selectedProject?.name || "风控建模 Agent"}</h1>
        </div>
        <div className="head-actions">
          <Badge
            variant={settings?.llm_enabled && settings?.api_key_configured ? "ok" : "neutral"}
            className="max-[1100px]:hidden"
          >
            {status}
          </Badge>
          <Badge variant="network" className="max-[1350px]:hidden">
            Notebook {settings?.notebook_network === false ? "关闭偏好" : "网络开启"}
          </Badge>
          {session.selectedProject && (
            <Button variant="outline" size="sm" onClick={session.showDataWorkbench}>
              数据 / 新 Y
            </Button>
          )}
          {session.decision && session.view === "workbench" && !session.dataMode && (
            <Badge variant="attention" className="max-[1100px]:hidden">
              等待你的确认
            </Badge>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={onToggleTheme}
            aria-label={theme === "dark" ? "切换到白天模式" : "切换到黑夜模式"}
            title={theme === "dark" ? "切换到白天模式" : "切换到黑夜模式"}
          >
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
        </div>
      </header>
      <Tabs
        value={session.view}
        onValueChange={(id) => session.setView(id as View)}
        className="contents"
      >
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
      <StagePanel run={session.run} decision={session.decision} events={session.events} />
    </>
  );
}
