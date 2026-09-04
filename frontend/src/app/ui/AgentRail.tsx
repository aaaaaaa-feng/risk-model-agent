import { lazy, Suspense } from "react";
import type { CSSProperties } from "react";
import { useChatRailState } from "../model/useChatRailState";
import { useColumnWidth } from "../model/useColumnWidth";
import type { ProjectSession } from "../model/useProjectSession";
import { runStageLabel } from "@/features/runs";
import type { Settings } from "@/features/settings";

const AgentChat = lazy(() =>
  import("@/features/chat/ui/AgentChat").then((module) => ({ default: module.AgentChat })),
);

interface Props {
  session: ProjectSession;
  settings: Settings | null;
  onProviderChange: () => void;
}

export function AgentRail({ session, settings, onProviderChange }: Props) {
  const rail = useChatRailState();
  const width = useColumnWidth({
    storageKey: "risk-agent-chat-rail-width",
    min: 240,
    max: 520,
    initial: typeof window !== "undefined" && window.innerWidth < 1440 ? 260 : 320,
    invert: true,
  });
  const contextPending = Boolean(session.runId && !session.run);
  const context = {
    run_id: session.run?.id || null,
    stage: session.run?.stage || null,
    decision_id: session.decision?.id || null,
  };
  const contextLabel = session.run
    ? `Run ${session.run.id.slice(-8)} · ${runStageLabel[session.run.stage] || session.run.stage}${session.decision ? " · 等待确认" : ""}`
    : contextPending
      ? `Run ${session.runId?.slice(-8)} · 正在验证归属与阶段`
      : "项目级（尚未选择 Run）";

  return (
    <>
      {!rail.collapsed && (
        <div
          className="col-resizer"
          aria-label="调整对话栏宽度"
          title="拖动调整宽度，双击恢复默认"
          {...width.resizerProps}
        />
      )}
      <aside
        className={`chat-rail ${rail.collapsed ? "collapsed" : rail.mode}`}
        style={
          rail.collapsed
            ? undefined
            : ({ "--chat-rail-width": `${width.width}px` } as CSSProperties)
        }
        aria-label="Agent 对话"
      >
        {rail.collapsed ? (
          <button
            className="chat-expand"
            type="button"
            aria-expanded={false}
            aria-label="展开 Agent 对话栏"
            title="展开右侧 Agent 对话栏"
            onClick={rail.toggle}
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
                onClick={rail.toggle}
              >
                ▸
              </button>
            </div>
            <Suspense fallback={<p className="chat-placeholder">正在加载 Agent 对话…</p>}>
              <AgentChat
                projectId={session.selectedId}
                context={context}
                contextLabel={contextLabel}
                contextPending={contextPending}
                settings={settings}
                onProviderChange={onProviderChange}
              />
            </Suspense>
          </>
        )}
      </aside>
    </>
  );
}
