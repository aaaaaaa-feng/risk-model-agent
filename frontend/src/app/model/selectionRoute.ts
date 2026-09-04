export type View = "workbench" | "report" | "history";

export interface SelectionRoute {
  projectId: string | null;
  runId: string | null;
  view: View;
  dataMode: boolean;
}

const views = new Set<View>(["workbench", "report", "history"]);
const identifierPattern = /^[A-Za-z][A-Za-z0-9_-]{0,159}$/;

function parseIdentifier(value: string | null): string | null {
  return value && identifierPattern.test(value) ? value : null;
}

/**
 * 解析只包含界面定位信息的 hash 路由。
 *
 * 项目数据和认证信息不会进入 URL；项目/Run ID 仍会在后端再次校验归属。
 */
export function parseSelectionHash(hash: string): SelectionRoute | null {
  const match = /^#\/(workbench|report|history)(?:\?(.*))?$/.exec(hash);
  if (!match) return null;
  const view = match[1] as View;
  const parameters = new URLSearchParams(match[2] || "");
  return {
    projectId: parseIdentifier(parameters.get("project")),
    runId: parseIdentifier(parameters.get("run")),
    view,
    dataMode: view === "workbench" && parameters.get("mode") === "data",
  };
}

export function serializeSelectionHash(route: SelectionRoute): string {
  const parameters = new URLSearchParams();
  if (route.projectId) parameters.set("project", route.projectId);
  if (route.runId) parameters.set("run", route.runId);
  if (route.view === "workbench" && route.dataMode) parameters.set("mode", "data");
  const query = parameters.toString();
  return `#/${route.view}${query ? `?${query}` : ""}`;
}

export function isView(value: string | null): value is View {
  return value !== null && views.has(value as View);
}
