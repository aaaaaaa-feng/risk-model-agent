import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { useRunEvents } from "./hooks";
import type { Decision, Project, ProjectDetail, Run, RunEvent, Settings } from "./types";
import { AgentChat } from "./components/AgentChat";
import { DataWorkbench } from "./components/DataWorkbench";
import { DecisionWorkbench } from "./components/DecisionWorkbench";
import { HistoryView } from "./components/HistoryView";
import { NewProjectDialog } from "./components/NewProjectDialog";
import { ProjectSidebar } from "./components/ProjectSidebar";
import { ReportView } from "./components/ReportView";
import { RunWorkbench } from "./components/RunWorkbench";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { StageRail } from "./components/StageRail";

type View = "workbench" | "report" | "history";

export function App() {
  const [projects,setProjects]=useState<Project[]>([]);const [selectedId,setSelectedId]=useState<string|null>(localStorage.getItem("risk-agent-project"));const [detail,setDetail]=useState<ProjectDetail|null>(null);const [runId,setRunId]=useState<string|null>(null);const [run,setRun]=useState<Run|null>(null);const [decision,setDecision]=useState<Decision|null>(null);const [events,setEvents]=useState<RunEvent[]>([]);const [settings,setSettings]=useState<Settings|null>(null);const [view,setView]=useState<View>("workbench");const [dataMode,setDataMode]=useState(false);const [createOpen,setCreateOpen]=useState(false);const [settingsOpen,setSettingsOpen]=useState(false);const [busy,setBusy]=useState(false);const [toast,setToast]=useState<{message:string;error:boolean}|null>(null);const tabs=useRef<Array<HTMLButtonElement|null>>([]);
  const notify=useCallback((message:string,error=false)=>{setToast({message,error});window.setTimeout(()=>setToast(null),3200);},[]);
  const loadSettings=useCallback(async()=>{try{const value=await api.get<any>("/providers/settings");setSettings(value.settings);}catch(error){notify(msg(error),true);}},[notify]);
  const loadProjects=useCallback(async()=>{try{const value=await api.get<{projects:Project[]}>("/projects");setProjects(value.projects);setSelectedId(current=>{if(current&&value.projects.some(p=>p.id===current))return current;return value.projects[0]?.id||null;});}catch(error){notify(msg(error),true);}},[notify]);
  const loadDetail=useCallback(async()=>{if(!selectedId){setDetail(null);return;}try{const value=await api.get<ProjectDetail>(`/projects/${selectedId}`);setDetail(value);setRunId(current=>{if(current&&value.runs.some(item=>item.id===current))return current;const active=value.runs.find(item=>["awaiting_decision","running","queued"].includes(item.status));return active?.id||value.runs[0]?.id||null;});}catch(error){notify(msg(error),true);}},[selectedId,notify]);
  const loadRun=useCallback(async()=>{if(!runId){setRun(null);setDecision(null);setEvents([]);return;}try{const [runValue,eventValue]=await Promise.all([api.get<any>(`/runs/${runId}`),api.get<any>(`/runs/${runId}/events`)]);setRun(runValue.run);setDecision(runValue.pending_decisions?.[0]||null);setEvents(current=>mergeEvents(current,eventValue.events));}catch(error){notify(msg(error),true);}},[runId,notify]);
  useEffect(()=>{loadProjects();loadSettings();},[]);
  useEffect(()=>{if(selectedId){localStorage.setItem("risk-agent-project",selectedId);setDataMode(false);}loadDetail();},[selectedId]);
  useEffect(()=>{loadRun();},[runId]);
  useEffect(()=>{const timer=window.setInterval(()=>{loadDetail();if(runId)loadRun();},5000);return()=>window.clearInterval(timer);},[loadDetail,loadRun,runId]);
  useRunEvents(runId,event=>{setEvents(current=>mergeEvents(current,[event]));if(["awaiting_decision","approved","succeeded","failed","blocked"].includes(event.status)){loadRun();loadDetail();}},()=>{loadRun();loadDetail();});
  const createProject=async(payload:any)=>{setBusy(true);try{const value=await api.post<any>("/projects",payload);await loadProjects();setSelectedId(value.project.id);setCreateOpen(false);setDataMode(true);notify("项目已创建；可开始导入本地数据");}catch(error){notify(msg(error),true);}finally{setBusy(false);}};
  const createDemo=async(mode:string)=>{setBusy(true);try{const value=await api.post<any>("/projects/demo",{mode,rows:1200,seed:20260821});await loadProjects();setSelectedId(value.project.id);setCreateOpen(false);setDataMode(true);notify("合成多表项目已就绪；三个 Y 可分别排队建模");}catch(error){notify(msg(error),true);}finally{setBusy(false);}};
  const retry=async()=>{if(!run||!detail)return;try{const value=await api.post<any>("/runs",{project_id:detail.project.id,target_task_id:run.target_task_id,mode:detail.project.mode});setRunId(value.run.id);setView("workbench");notify("新 Run 已进入队列");}catch(error){notify(msg(error),true);}};
  const selectProject=(id:string)=>{setSelectedId(id);setRunId(null);setEvents([]);setView("workbench");};
  const selectRun=(id:string)=>{setRunId(id);setDataMode(false);setView("workbench");};
  const activateTab=(next:View,index?:number)=>{setView(next);if(index!=null)tabs.current[index]?.focus();};
  const keyTab=(event:React.KeyboardEvent,index:number)=>{let next=index;if(event.key==="ArrowRight")next=(index+1)%3;else if(event.key==="ArrowLeft")next=(index+2)%3;else if(event.key==="Home")next=0;else if(event.key==="End")next=2;else return;event.preventDefault();activateTab((["workbench","report","history"] as View[])[next],next);};
  const selectedProject=detail?.project||projects.find(item=>item.id===selectedId)||null;
  const providerStatus=settings?.llm_enabled?(settings.api_key_configured?"LLM 已启用":"缺少密钥"):"本地降级";
  return <div className="app-shell">
    <ProjectSidebar projects={projects} selectedId={selectedId} settings={settings} onSelect={selectProject} onCreate={()=>setCreateOpen(true)} onSettings={()=>setSettingsOpen(true)} />
    <main className="main-column">
      <header className="app-header"><div className="head-title"><span>{selectedProject?`${selectedProject.mode==="semi_trusted"?"半信任":"完全信任"} · ${providerStatus}`:"LOCAL-FIRST"}</span><h1>{selectedProject?.name||"风控建模 Agent"}</h1></div><div className="head-actions"><span className={`tag ${settings?.llm_enabled&&settings?.api_key_configured?"ok":""}`}>{providerStatus}</span><span className="tag network">Notebook {settings?.notebook_network===false?"关闭偏好":"网络开启"}</span>{selectedProject&&<button className="button secondary compact" onClick={()=>{setDataMode(true);setView("workbench");}}>数据 / 新 Y</button>}{decision&&view==="workbench"&&!dataMode&&<span className="tag attention">等待你的确认</span>}</div></header>
      <nav className="primary-tabs" role="tablist" aria-label="项目主视图">{(["workbench","report","history"] as View[]).map((id,index)=><button key={id} id={`main-tab-${id}`} ref={node=>{tabs.current[index]=node}} role="tab" aria-selected={view===id} aria-controls="main-workspace" tabIndex={view===id?0:-1} onClick={()=>activateTab(id)} onKeyDown={event=>keyTab(event,index)}>{({workbench:"当前工作台",report:"产物报告",history:"历史 Run"} as Record<View,string>)[id]}</button>)}</nav>
      <section id="main-workspace" className="workspace" role="tabpanel" aria-labelledby={`main-tab-${view}`}>
        {!selectedProject&&<Welcome onCreate={()=>setCreateOpen(true)} />}
        {selectedProject&&detail&&view==="workbench"&&(dataMode||!run)?<DataWorkbench detail={detail} onRefresh={async()=>{await loadDetail();}} onRunsStarted={id=>{setRunId(id);setDataMode(false);}} notify={notify}/>:null}
        {selectedProject&&view==="workbench"&&!dataMode&&run&&decision?<DecisionWorkbench run={run} decision={decision} onResolved={()=>{loadRun();loadDetail();}} notify={notify}/>:null}
        {selectedProject&&view==="workbench"&&!dataMode&&run&&!decision?<RunWorkbench run={run} events={events} onRetry={retry}/>:null}
        {selectedProject&&view==="report"&&<ReportView project={selectedProject} run={run} notify={notify}/>}
        {selectedProject&&view==="history"&&<HistoryView runs={detail?.runs||[]} tasks={detail?.target_tasks||[]} selectedId={runId} onSelect={selectRun}/>}
      </section>
      <AgentChat projectId={selectedId} notify={notify}/>
    </main>
    <StageRail run={run} decision={decision} events={events}/>
    <NewProjectDialog open={createOpen} busy={busy} onClose={()=>setCreateOpen(false)} onCreate={createProject} onCreateDemo={createDemo}/>
    <SettingsDrawer open={settingsOpen} settings={settings} onClose={()=>setSettingsOpen(false)} onChanged={loadSettings} notify={notify}/>
    {toast&&<div className={`toast ${toast.error?"error":""}`} role="status">{toast.message}</div>}
  </div>;
}

function Welcome({onCreate}:{onCreate:()=>void}){return <div className="welcome"><span className="eyebrow">LOCAL RISK MODELING</span><h2>从一个可追溯的建模项目开始</h2><p>导入本地 CSV / Excel，多表关联，确认多个 0/1 Y，再由主 Agent、Reviewer 与确定性 Worker 完成闭环。</p><div className="welcome-grid"><div><b>01</b><strong>原始数据不出本机</strong><span>外部 LLM 只接收经过 DLP 的聚合 SafeEvidence。</span></div><div><b>02</b><strong>关键节点可确认</strong><span>半信任模式无需阅读代码，只确认业务选择。</span></div><div><b>03</b><strong>产物可独立评分</strong><span>同一报告数据导出 Web、Excel、HTML 与模型包。</span></div></div><button className="button primary" onClick={onCreate}>创建第一个项目</button></div>}
function mergeEvents(current:RunEvent[],incoming:RunEvent[]){const map=new Map(current.map(item=>[item.id,item]));incoming.forEach(item=>map.set(item.id,item));return [...map.values()].sort((a,b)=>a.sequence-b.sequence)}
function msg(error:unknown){return error instanceof Error?error.message:"操作失败"}
