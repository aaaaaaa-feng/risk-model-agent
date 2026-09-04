import { describe, expect, it } from "vitest";
import {
  parseSelectionHash,
  serializeSelectionHash,
  type SelectionRoute,
} from "./app/model/selectionRoute";
import { reconcileSelectionWithProjects } from "./app/model/useSelectionState";
import type { Project } from "@/features/projects";

describe("项目会话 hash 路由", () => {
  it("保存并恢复项目、Run、一级视图和数据工作台", () => {
    const route: SelectionRoute = {
      projectId: "prj_demo_01",
      runId: "run_demo_02",
      view: "workbench",
      dataMode: true,
    };
    const hash = serializeSelectionHash(route);

    expect(hash).toBe("#/workbench?project=prj_demo_01&run=run_demo_02&mode=data");
    expect(parseSelectionHash(hash)).toEqual(route);
  });

  it("报告和历史视图可被刷新、前进与后退恢复", () => {
    expect(parseSelectionHash("#/report?project=prj_1&run=run_1")).toEqual({
      projectId: "prj_1",
      runId: "run_1",
      view: "report",
      dataMode: false,
    });
    expect(parseSelectionHash("#/history?project=prj_1")).toEqual({
      projectId: "prj_1",
      runId: null,
      view: "history",
      dataMode: false,
    });
  });

  it("拒绝未知视图和不安全标识符", () => {
    expect(parseSelectionHash("#/settings?project=prj_1")).toBeNull();
    expect(parseSelectionHash("#/report?project=../../secret&run=run%2Fbad")).toEqual({
      projectId: null,
      runId: null,
      view: "report",
      dataMode: false,
    });
  });

  it("项目列表尚未加载时保留恢复路由，成功加载后才校正失效项目", () => {
    const restored: SelectionRoute = {
      projectId: "prj_saved",
      runId: "run_saved",
      view: "report",
      dataMode: false,
    };
    const projects = [{ id: "prj_other" }] as Project[];

    expect(reconcileSelectionWithProjects(restored, [], false)).toBe(restored);
    expect(reconcileSelectionWithProjects(restored, projects, false)).toBe(restored);
    expect(reconcileSelectionWithProjects(restored, projects, true)).toEqual({
      projectId: "prj_other",
      runId: null,
      view: "workbench",
      dataMode: false,
    });
  });

  it("服务端成功加载后仍保留有效的项目和 Run 选择", () => {
    const restored: SelectionRoute = {
      projectId: "prj_saved",
      runId: "run_saved",
      view: "history",
      dataMode: false,
    };
    const projects = [{ id: "prj_saved" }] as Project[];

    expect(reconcileSelectionWithProjects(restored, projects, true)).toBe(restored);
  });
});
