import type { ProjectDetail } from "@/features/projects";
import type { Decision, Run, RunEvent } from "@/features/runs";

export interface ProjectSessionResources {
  detail: ProjectDetail | null;
  run: Run | null;
  decision: Decision | null;
  events: RunEvent[];
}

/**
 * 请求取消只能阻止旧响应落库，不能阻止路由切换后的第一个 React render 仍持有旧 state。
 * 渲染边界因此还要按项目/Run 归属做一次同步过滤，杜绝前进后退时短暂串页。
 */
export function alignProjectSessionResources(
  selectedId: string | null,
  runId: string | null,
  resources: ProjectSessionResources,
): ProjectSessionResources {
  const detail = resources.detail?.project.id === selectedId ? resources.detail : null;
  const loadedRun =
    resources.run?.id === runId && resources.run.project_id === selectedId ? resources.run : null;
  const summarizedRun = (detail?.runs || []).find((item) => item.id === runId) || null;
  const run = loadedRun || summarizedRun;
  const decision = resources.decision?.run_id === run?.id && run ? resources.decision : null;
  const events = run ? resources.events.filter((event) => event.run_id === run.id) : [];
  return { detail, run, decision, events };
}
