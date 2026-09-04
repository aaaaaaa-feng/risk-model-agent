import type { CSSProperties } from "react";
import type { Project } from "../types";

interface Props {
  projects: Project[];
  selectedId: string | null;
  open: boolean;
  onToggle: () => void;
  /** 展开时的宽度（拖拽分隔条实时调整）；折叠时忽略 */
  width?: number;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onSettings: () => void;
}

export function ProjectSidebar({
  projects,
  selectedId,
  open,
  onToggle,
  width,
  onSelect,
  onCreate,
  onSettings,
}: Props) {
  const selected = projects.find((project) => project.id === selectedId);
  return (
    <aside
      className={`sidebar ${open ? "" : "collapsed"}`}
      style={open && width ? ({ "--sidebar-width": `${width}px` } as CSSProperties) : undefined}
      aria-label="项目列表"
    >
      <div className="brand">
        <div className="brand-mark">RM</div>
        {open && (
          <div>
            <strong>风控建模 Agent</strong>
            <span>LOCAL WORKBENCH</span>
          </div>
        )}
        <button
          className="sidebar-toggle"
          type="button"
          aria-expanded={open}
          aria-label={open ? "收起项目列表" : "展开项目列表"}
          title={open ? "收起左侧项目列表" : "展开左侧项目列表"}
          onClick={onToggle}
        >
          {open ? "«" : "»"}
        </button>
      </div>
      {open ? (
        <div className="side-scroll">
          <div className="eyebrow side-label">项目</div>
          <button
            className="new-project"
            type="button"
            onClick={onCreate}
            title="创建一个新的风控建模项目"
          >
            ＋ 新建项目
          </button>
          <div className="project-list">
            {projects.length === 0 && (
              <p className="sidebar-empty">还没有项目。创建项目后即可导入本地数据。</p>
            )}
            {projects.map((project) => (
              <button
                key={project.id}
                className={`project-item ${project.id === selectedId ? "active" : ""}`}
                type="button"
                onClick={() => onSelect(project.id)}
                aria-current={project.id === selectedId ? "page" : undefined}
                title={`打开项目：${project.name}`}
              >
                <strong>{project.name}</strong>
                <small>
                  {statusLabel(project.status)} ·{" "}
                  {project.mode === "semi_trusted" ? "半信任" : "完全信任"}
                </small>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="side-scroll compact">
          <button
            className="new-project icon-only"
            type="button"
            onClick={onCreate}
            aria-label="新建项目"
            title="新建项目"
          >
            ＋
          </button>
          {selected && (
            <button
              className="project-item compact active"
              type="button"
              onClick={() => onSelect(selected.id)}
              title={selected.name}
              aria-current="page"
            >
              <strong>{selected.name.slice(0, 1)}</strong>
            </button>
          )}
        </div>
      )}
      {open && (
        <div className="side-footer">
          <button
            className="settings-open"
            type="button"
            onClick={onSettings}
            title="打开模型 API、建模、网络、工作文件夹与备份设置"
          >
            设置中心
          </button>
        </div>
      )}
      {!open && (
        <div className="side-footer compact">
          <button
            className="settings-open icon-only"
            type="button"
            onClick={onSettings}
            aria-label="设置中心"
            title="设置中心"
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
        </div>
      )}
    </aside>
  );
}

function statusLabel(status: string) {
  return (
    (
      {
        active: "进行中",
        data_imported: "已导入",
        archived: "已归档",
        trashed: "回收站",
      } as Record<string, string>
    )[status] || status
  );
}
