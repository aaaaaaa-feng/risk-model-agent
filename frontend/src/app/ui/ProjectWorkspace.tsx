import { lazy, Suspense } from "react";
import type { ProjectSession } from "../model/useProjectSession";
import { Button } from "@/shared/ui/button";

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

interface Props {
  session: ProjectSession;
  onCreate: () => void;
}

export function ProjectWorkspace({ session, onCreate }: Props) {
  return (
    <section
      id="main-workspace"
      className="workspace"
      role="tabpanel"
      aria-labelledby={`tab-${session.view}`}
    >
      <Suspense fallback={<div className="loading-panel">正在加载当前视图…</div>}>
        {!session.selectedProject && <Welcome onCreate={onCreate} />}
        {session.selectedProject &&
        session.detail &&
        session.view === "workbench" &&
        (session.dataMode || !session.run) ? (
          <DataWorkbench
            detail={session.detail}
            onRefresh={session.refreshDetail}
            onRunsStarted={session.selectRun}
          />
        ) : null}
        {session.selectedProject &&
        session.view === "workbench" &&
        !session.dataMode &&
        session.run &&
        session.decision ? (
          <DecisionWorkbench
            run={session.run}
            decision={session.decision}
            onResolved={() => {
              void session.refreshRun();
              void session.refreshDetail();
            }}
          />
        ) : null}
        {session.selectedProject &&
        session.view === "workbench" &&
        !session.dataMode &&
        session.run &&
        !session.decision ? (
          <RunWorkbench run={session.run} events={session.events} onRetry={session.retry} />
        ) : null}
        {session.selectedProject && session.view === "report" && (
          <ReportView project={session.selectedProject} run={session.run} />
        )}
        {session.selectedProject && session.view === "history" && (
          <HistoryView
            runs={session.detail?.runs || []}
            tasks={session.detail?.target_tasks || []}
            selectedId={session.runId}
            onSelect={session.selectRun}
          />
        )}
      </Suspense>
    </section>
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
