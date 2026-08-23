import type { CSSProperties } from "react";
import type { Project, Settings } from "../types";

interface Props {
  projects: Project[];
  selectedId: string | null;
  settings: Settings | null;
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
  settings,
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
      style={
        open && width
          ? ({ "--sidebar-width": `${width}px` } as CSSProperties)
          : undefined
      }
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
          onClick={onToggle}
        >
          {open ? "«" : "»"}
        </button>
      </div>
      {open ? (
        <div className="side-scroll">
          <div className="eyebrow side-label">项目</div>
          <button className="new-project" type="button" onClick={onCreate}>
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
          <div className="network-note">
            <b>Notebook 网络：{settings?.notebook_network === false ? "关闭偏好" : "开启"}</b>
            <p>产品与 LLM 不主动上传原始数据；用户代码和第三方包并非安全沙箱。</p>
          </div>
          <button className="settings-open" type="button" onClick={onSettings}>
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
            ⚙
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
