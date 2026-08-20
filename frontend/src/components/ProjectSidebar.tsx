import type { Project, Settings } from "../types";

interface Props {
  projects: Project[];
  selectedId: string | null;
  settings: Settings | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onSettings: () => void;
}

export function ProjectSidebar({ projects, selectedId, settings, onSelect, onCreate, onSettings }: Props) {
  return <aside className="sidebar" aria-label="项目列表">
    <div className="brand"><div className="brand-mark">RM</div><div><strong>风控建模 Agent</strong><span>LOCAL WORKBENCH</span></div></div>
    <div className="side-scroll">
      <div className="eyebrow side-label">项目</div>
      <button className="new-project" type="button" onClick={onCreate}>＋ 新建项目</button>
      <div className="project-list">
        {projects.length === 0 && <p className="sidebar-empty">还没有项目。创建项目后即可导入本地数据。</p>}
        {projects.map(project => <button
          key={project.id}
          className={`project-item ${project.id === selectedId ? "active" : ""}`}
          type="button"
          onClick={() => onSelect(project.id)}
          aria-current={project.id === selectedId ? "page" : undefined}
        >
          <strong>{project.name}</strong>
          <small>{statusLabel(project.status)} · {project.mode === "semi_trusted" ? "半信任" : "完全信任"}</small>
        </button>)}
      </div>
    </div>
    <div className="side-footer">
      <div className="network-note">
        <b>Notebook 网络：{settings?.notebook_network === false ? "关闭偏好" : "开启"}</b>
        <p>产品与 LLM 不主动上传原始数据；用户代码和第三方包并非安全沙箱。</p>
      </div>
      <button className="settings-open" type="button" onClick={onSettings}>设置中心</button>
    </div>
  </aside>;
}

function statusLabel(status: string) {
  return ({ active: "进行中", data_imported: "已导入", archived: "已归档", trashed: "回收站" } as Record<string, string>)[status] || status;
}
