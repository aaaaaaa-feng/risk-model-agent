"use strict";

const state = {
  projects: [],
  currentProject: null,
  activeModel: null,
  polling: false,
  busy: false,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const STATUS_LABELS = {
  created: "已创建",
  uploaded: "已导入",
  profiled: "体检完成",
  awaiting_approval: "待人工确认",
  approved: "方案已批准",
  training: "训练中",
  completed: "训练完成",
  failed: "运行失败",
  blocked: "存在阻断项",
};

const TYPE_LABELS = {
  numeric: "数值",
  categorical: "类别",
  datetime: "日期时间",
  boolean: "布尔",
  text: "文本",
  empty: "空字段",
  unknown: "待识别",
};

const MODEL_LABELS = {
  dummy: "先验概率基线",
  logistic_regression: "逻辑回归",
  random_forest: "随机森林",
  hist_gradient_boosting: "梯度提升树",
};

const CONFIRMATION_LABELS = {
  target_definition: ["确认标签定义", "已核验目标字段、坏样本取值与观察窗口的业务含义。"],
  feature_and_leakage_review: ["确认字段与泄漏审查", "已核验字段产生时间，并理解自动泄漏检测只是启发式提醒。"],
  split_strategy: ["确认数据切分", "已理解随机留出或时间留出的适用范围与局限。"],
  model_plan: ["确认本次离线方案", "认可候选模型、预处理和主指标仅用于本次离线实验。"],
};

const ISSUE_LABELS = {
  EMPTY_DATASET: "数据集没有有效记录。",
  PROFILE_TRUNCATED: "数据体检基于受限行数，可能遗漏低频问题。",
  HIGH_MISSING_RATE: "字段缺失率达到或超过 50%。",
  SENSITIVE_COLUMN: "字段名可能包含个人或敏感信息。",
  TARGET_HAS_MISSING_VALUES: "目标字段存在缺失值，需要先明确处理策略。",
  TARGET_NOT_BINARY: "目标字段包含预期好坏标签之外的取值。",
  TARGET_CLASS_MISSING: "目标字段没有同时包含好、坏两类样本。",
  TARGET_MINORITY_TOO_SMALL: "少数类别样本不足 20 条，无法稳定训练。",
  TIME_COLUMN_REQUIRED: "时间切分必须指定明确的申请时间字段。",
  TIME_COLUMN_NOT_FOUND: "指定的时间字段不在当前数据集中。",
  NO_TIME_BASED_OOT: "当前采用随机留出，不是独立时间外验证。",
  SUSPECTED_POST_OUTCOME_FEATURE: "字段名疑似表示贷后结果，可能产生目标泄漏。",
  TARGET_COPY_DETECTED: "字段值疑似直接复制目标标签。",
  FEATURE_DROPPED: "字段已被默认治理策略排除。",
};

const API_ERROR_LABELS = {
  INVALID_MAX_ROWS: "读取行数配置无效。",
  UNSUPPORTED_DATASET_TYPE: "V1 仅支持 CSV 文件。",
  DATASET_NOT_FOUND: "没有找到数据文件。",
  CSV_PARSE_FAILED: "CSV 无法解析，请检查分隔符与文件结构。",
  CSV_ENCODING_UNSUPPORTED: "CSV 编码不支持，请使用 UTF-8 或 GB18030。",
  CSV_HAS_NO_COLUMNS: "CSV 没有可用字段。",
  EMPTY_DATASET: "CSV 文件为空。",
  TARGET_COLUMN_REQUIRED: "请先选择目标字段。",
  TARGET_COLUMN_NOT_FOUND: "目标字段不在当前数据中。",
  TARGET_LABELS_IDENTICAL: "好坏样本标签不能相同。",
  PLAN_BLOCKED: "当前方案仍有阻断项，不能批准。",
  PLAN_HASH_MISMATCH: "方案已经变化，请刷新后重新确认。",
  CONFIRMATIONS_INCOMPLETE: "请完成全部人工确认项。",
  TRAINING_IN_PROGRESS: "当前已有训练任务正在运行。",
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindEvents();
  await loadProjects();
}

function bindEvents() {
  $("#uploadForm").addEventListener("submit", createProjectFromUpload);
  $("#sampleButton").addEventListener("click", createSampleProject);
  $("#newProjectButton").addEventListener("click", showStartPanel);
  $("#refreshButton").addEventListener("click", () => refreshCurrentProject(true));
  $("#planForm").addEventListener("submit", submitPlan);
  $("#approveButton").addEventListener("click", approvePlan);
  $("#trainButton").addEventListener("click", trainModel);
  $("#agentForm").addEventListener("submit", sendAgentMessage);
  $("#agentInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#agentForm").requestSubmit();
    }
  });
  $("#datasetFile").addEventListener("change", updateSelectedFile);
  $("#columnSearch").addEventListener("input", filterColumnChips);
  $("#targetColumn").addEventListener("change", () => {
    updateTargetValueHints();
    syncProtectedColumns();
  });
  $("#timeColumn").addEventListener("change", syncProtectedColumns);
  $("#excludedColumns").addEventListener("change", updateExcludedCount);
  $("#confirmationList").addEventListener("change", updateApproveButton);

  $("#projectList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-project-id]");
    if (button) selectProject(button.dataset.projectId);
  });

  $("#workflowSteps").addEventListener("click", (event) => {
    const button = event.target.closest("[data-scroll-to]");
    if (!button || !state.currentProject) return;
    const section = document.getElementById(button.dataset.scrollTo);
    if (section) section.scrollIntoView({ behavior: "smooth", block: "start" });
    closeMobileNav();
  });

  $("#modelTabs").addEventListener("click", (event) => {
    const tab = event.target.closest("[data-model-id]");
    if (!tab) return;
    state.activeModel = tab.dataset.modelId;
    renderRunResults(state.currentProject);
  });

  $("#suggestedPrompts").addEventListener("click", (event) => {
    const button = event.target.closest("[data-prompt]");
    if (!button) return;
    $("#agentInput").value = button.dataset.prompt;
    $("#agentForm").requestSubmit();
  });

  $("#agentCollapseButton").addEventListener("click", toggleAgentPanel);
  $("#mobileNavButton").addEventListener("click", toggleMobileNav);

  const dropZone = $("#dropZone");
  ["dragenter", "dragover"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.remove("is-dragging");
    });
  });
  dropZone.addEventListener("drop", (event) => {
    const file = event.dataTransfer && event.dataTransfer.files[0];
    if (!file) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    $("#datasetFile").files = transfer.files;
    updateSelectedFile();
  });
}

async function apiRequest(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      headers: options.body instanceof FormData
        ? (options.headers || {})
        : { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
  } catch (error) {
    throw new Error("无法连接本地服务，请确认应用仍在运行。", { cause: error });
  }

  const contentType = response.headers.get("content-type") || "";
  let payload = null;
  if (contentType.includes("application/json")) {
    payload = await response.json().catch(() => null);
  } else {
    const text = await response.text().catch(() => "");
    payload = text ? { message: text } : null;
  }

  if (!response.ok) {
    const errorPayload = payload && (payload.error || payload);
    const code = errorPayload && errorPayload.code;
    const message = API_ERROR_LABELS[code]
      || (errorPayload && (errorPayload.message || errorPayload.detail))
      || `请求失败（HTTP ${response.status}）`;
    const error = new Error(message);
    error.code = code;
    error.details = errorPayload && errorPayload.details;
    throw error;
  }
  return payload;
}

async function loadProjects(preferredId) {
  try {
    const payload = await apiRequest("/api/projects");
    state.projects = Array.isArray(payload) ? payload : (payload && payload.projects) || [];
    renderProjectList();

    const candidateId = preferredId
      || (state.currentProject && state.currentProject.id)
      || (state.projects[0] && state.projects[0].id);
    if (candidateId) {
      await selectProject(candidateId, { quiet: true });
    } else {
      showStartPanel();
    }
  } catch (error) {
    renderProjectList();
    showStartPanel();
    showToast("项目列表读取失败", error.message, "error");
  }
}

function renderProjectList() {
  const container = $("#projectList");
  if (!state.projects.length) {
    container.innerHTML = '<p class="project-list-empty">还没有项目<br>从一份 CSV 开始</p>';
    return;
  }

  const activeId = state.currentProject && state.currentProject.id;
  container.innerHTML = state.projects.map((project) => {
    const activeClass = project.id === activeId ? " is-active" : "";
    const name = escapeHTML(project.name || "未命名项目");
    const status = escapeHTML(statusLabel(project.status));
    const initial = escapeHTML(String(project.name || "项").trim().slice(0, 1).toUpperCase());
    return `
      <button class="project-item${activeClass}" type="button" data-project-id="${escapeHTML(project.id)}">
        <span class="project-avatar">${initial}</span>
        <span><strong>${name}</strong><small>${status}</small></span>
      </button>`;
  }).join("");
}

async function selectProject(projectId, options = {}) {
  if (!projectId) return;
  if (!options.quiet) setLoading(true, "正在读取项目…");
  try {
    const payload = await apiRequest(`/api/projects/${encodeURIComponent(projectId)}`);
    state.currentProject = unwrapProject(payload);
    state.activeModel = null;
    upsertProjectSummary(state.currentProject);
    renderCurrentProject();
    closeMobileNav();
  } catch (error) {
    showToast("项目读取失败", error.message, "error");
  } finally {
    if (!options.quiet) setLoading(false);
  }
}

async function refreshCurrentProject(showFeedback = false) {
  if (!state.currentProject) return null;
  try {
    const payload = await apiRequest(`/api/projects/${encodeURIComponent(state.currentProject.id)}`);
    state.currentProject = unwrapProject(payload);
    upsertProjectSummary(state.currentProject);
    renderCurrentProject();
    if (showFeedback) showToast("已刷新", "项目状态已与本地产物同步。", "success");
    return state.currentProject;
  } catch (error) {
    if (showFeedback) showToast("刷新失败", error.message, "error");
    throw error;
  }
}

function unwrapProject(payload) {
  if (payload && payload.project) return payload.project;
  return payload || {};
}

function upsertProjectSummary(project) {
  if (!project || !project.id) return;
  const index = state.projects.findIndex((item) => item.id === project.id);
  const summary = {
    ...(index >= 0 ? state.projects[index] : {}),
    id: project.id,
    name: project.name,
    status: project.status,
    dataset: project.dataset,
  };
  if (index >= 0) state.projects.splice(index, 1, summary);
  else state.projects.unshift(summary);
  renderProjectList();
}

async function ensureProjectFromResponse(payload) {
  const candidate = unwrapProject(payload);
  if (candidate && candidate.id && candidate.dataset && candidate.profile) {
    state.currentProject = candidate;
    upsertProjectSummary(candidate);
    renderCurrentProject();
    return candidate;
  }
  return refreshCurrentProject(false);
}

function showStartPanel() {
  state.currentProject = null;
  state.activeModel = null;
  $("#startPanel").classList.remove("is-hidden");
  $("#projectPanel").classList.add("is-hidden");
  renderProjectList();
  resetWorkflowSteps();
  closeMobileNav();
  requestAnimationFrame(() => $("#projectName").focus());
}

async function createProjectFromUpload(event) {
  event.preventDefault();
  if (state.busy) return;
  const form = event.currentTarget;
  const file = $("#datasetFile").files[0];
  const name = $("#projectName").value.trim();
  if (!name || !file) {
    showToast("信息不完整", "请填写项目名称并选择 CSV 文件。", "error");
    return;
  }
  if (!file.name.toLowerCase().endsWith(".csv")) {
    showToast("文件格式不支持", "V1 仅支持 CSV 文件。", "error");
    return;
  }

  const body = new FormData();
  body.append("name", name);
  body.append("file", file, file.name);
  setLoading(true, "正在导入并体检数据…");
  state.busy = true;
  try {
    const payload = await apiRequest("/api/projects", { method: "POST", body });
    state.currentProject = unwrapProject(payload);
    upsertProjectSummary(state.currentProject);
    renderCurrentProject();
    form.reset();
    resetFilePicker();
    showToast("数据体检完成", "请核验风险提示并配置目标标签。", "success");
  } catch (error) {
    showToast("导入失败", error.message, "error");
  } finally {
    state.busy = false;
    setLoading(false);
  }
}

async function createSampleProject() {
  if (state.busy) return;
  state.busy = true;
  setLoading(true, "正在载入框架测试数据…");
  try {
    const sampleResponse = await fetch("/api/sample.csv");
    if (!sampleResponse.ok) throw new Error("演示 CSV 暂时不可用。");
    const blob = await sampleResponse.blob();
    const body = new FormData();
    body.append("name", `框架测试演示 · ${new Date().toLocaleDateString("zh-CN")}`);
    // Keep the server's canonical demo filename so exported reports retain
    // the synthetic-data evidence label as well as the on-screen banner.
    body.append("file", new File([blob], "risk_model_agent_demo.csv", { type: "text/csv" }));
    const payload = await apiRequest("/api/projects", { method: "POST", body });
    state.currentProject = unwrapProject(payload);
    upsertProjectSummary(state.currentProject);
    renderCurrentProject();
    showToast("演示数据已载入", "本次结果仅用于验证框架流程。", "success");
  } catch (error) {
    showToast("演示数据载入失败", error.message, "error");
  } finally {
    state.busy = false;
    setLoading(false);
  }
}

function renderCurrentProject() {
  const project = state.currentProject;
  if (!project || !project.id) {
    showStartPanel();
    return;
  }
  $("#startPanel").classList.add("is-hidden");
  $("#projectPanel").classList.remove("is-hidden");

  $("#projectTitle").textContent = project.name || "未命名项目";
  $("#projectIdLabel").textContent = shortId(project.id);
  renderProjectStatus(project.status);
  renderHeaderMeta(project);
  $("#demoBanner").classList.toggle("is-hidden", !isDemoProject(project));

  renderProfile(project);
  renderPlanForm(project);
  renderPlan(project);
  renderApproval(project);
  renderTraining(project);
  renderWorkflow(project);
  renderProjectList();
}

function renderProjectStatus(status) {
  const badge = $("#projectStatus");
  badge.textContent = statusLabel(status);
  badge.className = "project-status";
  if (["completed", "approved"].includes(status)) badge.classList.add("is-complete");
  if (["awaiting_approval", "training"].includes(status)) badge.classList.add("is-warning");
  if (["failed", "blocked"].includes(status)) badge.classList.add("is-error");
}

function renderHeaderMeta(project) {
  const profile = project.profile || {};
  const dataset = project.dataset || {};
  const parts = [];
  const filename = dataset.original_filename || dataset.filename || dataset.name;
  if (filename) parts.push(filename);
  if (profile.row_count != null) parts.push(`${formatInteger(profile.row_count)} 行`);
  if (profile.column_count != null) parts.push(`${formatInteger(profile.column_count)} 列`);
  const hash = dataset.sha256 || dataset.source_sha256 || profile.source_sha256;
  if (hash) parts.push(`SHA-256 ${String(hash).slice(0, 10)}…`);
  $("#projectMeta").textContent = parts.join(" · ") || "本地建模项目";
}

function renderProfile(project) {
  const profile = project.profile || {};
  const dataset = project.dataset || {};
  const columns = getColumnEntries(project);
  const highMissing = columns.filter(([, item]) => item.is_high_missing).length;
  const suspectedIds = columns.filter(([, item]) => item.is_suspected_id).length;
  const fileSize = dataset.size_bytes != null ? formatBytes(dataset.size_bytes) : "—";
  const cards = [
    ["样本行数", formatInteger(profile.row_count), profile.truncated ? "体检已截断" : "当前体检范围"],
    ["字段数量", formatInteger(profile.column_count || columns.length), "含目标候选字段"],
    ["重复记录", formatInteger(profile.duplicate_row_count), "按整行判断"],
    ["高缺失字段", formatInteger(highMissing), "缺失率 ≥ 50%"],
    ["疑似 ID / 敏感字段", formatInteger(suspectedIds), fileSize],
  ];
  $("#profileMetrics").innerHTML = cards.map(([label, value, note]) => `
    <article class="metric-card"><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong><small>${escapeHTML(note)}</small></article>
  `).join("");

  const blockers = getBlockingIssues(project);
  const warnings = getWarnings(project);
  renderIssueList("blockingIssues", "blockingCount", blockers, "未发现阻断项，可以继续完善方案。", true);
  renderIssueList("warningIssues", "warningCount", warnings, "当前规则未命中风险提示；仍需人工核验字段血缘。", false);

  const profileState = $("#profileState");
  profileState.className = "section-state is-complete";
  profileState.textContent = blockers.length ? "体检完成 · 有阻断" : "体检完成";
  if (blockers.length) profileState.className = "section-state is-warning";

  $("#columnSummary").textContent = `${columns.length} 个字段；风险识别为启发式筛查`;
  $("#columnTableBody").innerHTML = columns.length
    ? columns.map(([name, item]) => renderColumnRow(name, item)).join("")
    : '<tr><td colspan="5">暂无字段画像。</td></tr>';
}

function renderIssueList(listId, countId, issues, emptyText, blocking) {
  $("#" + countId).textContent = String(issues.length);
  $("#" + listId).innerHTML = issues.length
    ? issues.map((issue) => `<li>${escapeHTML(issueText(issue))}</li>`).join("")
    : `<li class="no-issue">✓ ${escapeHTML(emptyText)}</li>`;
  if (blocking) $("#approvalBlocker").classList.toggle("is-hidden", issues.length === 0);
}

function renderColumnRow(name, item) {
  const flags = [];
  if (item.is_sensitive) flags.push('<span class="risk-tag risk-tag--danger">敏感</span>');
  if (item.is_suspected_id) flags.push('<span class="risk-tag">疑似 ID</span>');
  if (item.is_high_missing) flags.push('<span class="risk-tag">高缺失</span>');
  if (item.is_high_cardinality) flags.push('<span class="risk-tag">高基数</span>');
  if (item.is_constant) flags.push('<span class="risk-tag">常量</span>');
  if (!flags.length) flags.push('<span class="risk-tag risk-tag--safe">未命中规则</span>');
  return `<tr>
    <td>${escapeHTML(name)}</td>
    <td><span class="type-tag">${escapeHTML(typeLabel(item.inferred_type || item.type))}</span></td>
    <td>${formatPercent(item.missing_rate)}</td>
    <td>${formatInteger(item.unique_count)}</td>
    <td>${flags.join("")}</td>
  </tr>`;
}

function renderPlanForm(project) {
  const columns = getColumnEntries(project);
  const plan = project.plan || {};
  const target = plan.target || {};
  const split = plan.split || {};
  const targetSelect = $("#targetColumn");
  const timeSelect = $("#timeColumn");
  const targetValue = target.column || "";
  const timeValue = split.time_column || "";

  targetSelect.innerHTML = '<option value="">请选择二分类标签</option>'
    + columns.map(([name]) => `<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join("");
  timeSelect.innerHTML = '<option value="">未指定，采用分层随机切分</option>'
    + columns.map(([name, item]) => `<option value="${escapeHTML(name)}">${escapeHTML(name)}${item.is_datetime ? " · 日期" : ""}</option>`).join("");
  targetSelect.value = targetValue;
  timeSelect.value = timeValue;
  $("#positiveLabel").value = target.positive_label != null ? String(target.positive_label) : "";
  updateTargetValueHints();

  const planFeatures = plan.features || {};
  const dropped = new Set(planFeatures.dropped_columns || []);
  const included = new Set(planFeatures.included_columns || []);
  $("#excludedColumns").innerHTML = columns.map(([name, item]) => {
    const policySuggested = item.is_suspected_id || item.is_sensitive || item.is_constant || item.is_high_missing || item.is_high_cardinality;
    // The plan stores effective included/dropped features, while the API request
    // stores user exclusions only through that effective set. Reconstruct them
    // so a harmless refresh cannot silently add a manually excluded field back.
    const excludedByCurrentPlan = Boolean(plan.version) && name !== targetValue && !included.has(name);
    const checked = dropped.has(name) || excludedByCurrentPlan || (!plan.version && policySuggested);
    return `<label class="checkbox-chip" data-column-name="${escapeHTML(name.toLowerCase())}">
      <input type="checkbox" value="${escapeHTML(name)}" ${checked ? "checked" : ""}>
      <span>${escapeHTML(name)}</span>
    </label>`;
  }).join("");
  syncProtectedColumns();
  updateExcludedCount();
}

function renderPlan(project) {
  const plan = project.plan;
  const output = $("#planOutput");
  const planState = $("#planState");
  if (!plan || !plan.version) {
    output.classList.add("is-hidden");
    planState.className = "section-state";
    planState.textContent = "待配置";
    return;
  }

  output.classList.remove("is-hidden");
  const blocked = getBlockingIssues(project).length > 0;
  planState.className = blocked ? "section-state is-warning" : "section-state is-complete";
  planState.textContent = blocked ? "方案有阻断" : `方案 v${plan.version}`;
  $("#planVersion").textContent = `版本 v${plan.version}`;
  $("#planHash").textContent = plan.plan_hash || "未提供哈希";
  $("#planHash").title = plan.plan_hash || "";

  const target = plan.target || {};
  const features = plan.features || {};
  const split = plan.split || {};
  const summaries = [
    ["目标字段", target.column || "—"],
    ["坏样本取值", target.positive_label != null ? target.positive_label : "—"],
    ["纳入字段", `${(features.included_columns || []).length} 个`],
    ["留出策略", splitLabel(split.method)],
    ["留出比例", formatPercent(split.test_size)],
    ["随机种子", split.random_state != null ? split.random_state : "—"],
    ["拟合边界", "仅训练集"],
    ["主指标", metricLabel((plan.selection || {}).primary_metric || "roc_auc")],
  ];
  $("#planSummaryGrid").innerHTML = summaries.map(([label, value]) => `
    <div class="plan-summary-item"><span>${escapeHTML(label)}</span><strong title="${escapeHTML(value)}">${escapeHTML(value)}</strong></div>
  `).join("");

  const candidates = plan.candidates || [];
  $("#candidateList").innerHTML = candidates.length
    ? candidates.map((name) => `<span class="model-chip">${escapeHTML(modelLabel(name))}</span>`).join("")
    : '<span class="model-chip">等待候选模型配置</span>';

  const limitations = plan.limitations || [];
  const warnings = plan.warnings || [];
  $("#planNotes").innerHTML = `
    <strong>方案边界</strong>
    <p>${escapeHTML(limitations[0] || "离线实验指标不代表生产效果、业务收益或合规结论。")}</p>
    <p>已记录 ${warnings.length} 条风险提示，所有泄漏检测均需业务人员复核。</p>`;
}

function renderApproval(project) {
  const plan = project.plan || {};
  const required = Array.isArray(plan.required_confirmations) ? plan.required_confirmations : [];
  const approved = isPlanApproved(project);
  const blockers = getBlockingIssues(project);
  const empty = $("#confirmationEmpty");
  const list = $("#confirmationList");
  empty.classList.toggle("is-hidden", required.length > 0);
  list.innerHTML = required.map((item) => {
    const id = typeof item === "string" ? item : (item.id || item.name);
    const defaultCopy = CONFIRMATION_LABELS[id] || [item.label || id, item.description || "请核验并确认该事项。"];
    return `<label class="confirmation-item">
      <input type="checkbox" value="${escapeHTML(id)}" ${approved ? "checked disabled" : ""}>
      <span><strong>${escapeHTML(defaultCopy[0])}</strong><small>${escapeHTML(defaultCopy[1])}</small></span>
    </label>`;
  }).join("");

  $("#approvalBlocker").classList.toggle("is-hidden", blockers.length === 0);
  const approvalState = $("#approvalState");
  approvalState.className = "section-state";
  if (approved) {
    approvalState.classList.add("is-complete");
    approvalState.textContent = "已批准";
  } else if (blockers.length) {
    approvalState.classList.add("is-warning");
    approvalState.textContent = "被阻断";
  } else {
    approvalState.textContent = plan.version ? "待逐项确认" : "等待方案";
  }

  $("#approvalMeta").innerHTML = `
    <div><dt>方案版本</dt><dd>${plan.version ? `v${escapeHTML(plan.version)}` : "—"}</dd></div>
    <div><dt>方案哈希</dt><dd title="${escapeHTML(plan.plan_hash || "")}">${plan.plan_hash ? `${escapeHTML(plan.plan_hash.slice(0, 8))}…` : "—"}</dd></div>
    <div><dt>批准状态</dt><dd>${approved ? "已绑定当前版本" : "未批准"}</dd></div>`;

  const button = $("#approveButton");
  if (approved) {
    button.disabled = true;
    button.querySelector("strong").textContent = "当前方案已批准";
    button.querySelector("small").textContent = "批准记录已绑定方案哈希";
  } else {
    button.querySelector("strong").textContent = "批准当前方案";
    button.querySelector("small").textContent = "锁定版本后才能开始训练";
    updateApproveButton();
  }
}

function updateApproveButton() {
  const project = state.currentProject || {};
  const plan = project.plan || {};
  const required = Array.isArray(plan.required_confirmations) ? plan.required_confirmations.length : 0;
  const checked = $$('#confirmationList input[type="checkbox"]:checked').length;
  const blocked = getBlockingIssues(project).length > 0;
  $("#approveButton").disabled = !plan.version || blocked || !required || checked !== required || isPlanApproved(project);
}

function renderTraining(project) {
  const approved = isPlanApproved(project);
  const status = project.status;
  const training = status === "training";
  const failed = status === "failed";
  const gate = $("#trainGate");
  const button = $("#trainButton");
  const runState = $("#runState");
  gate.className = "train-gate";
  runState.className = "section-state";

  if (training) {
    gate.classList.add("is-running");
    $("#trainGateTitle").textContent = "本地训练正在运行";
    $("#trainGateText").textContent = "请等待当前不可变运行完成，不要重复提交。";
    button.disabled = true;
    button.querySelector("span").textContent = "训练中…";
    runState.classList.add("is-running");
    runState.textContent = "训练中";
  } else if (approved) {
    gate.classList.add("is-ready");
    $("#trainGateTitle").textContent = failed ? "上次运行失败，可按已批准方案重试" : "方案已锁定，可以开始训练";
    $("#trainGateText").textContent = "训练严格使用已批准方案；Agent 不会自动触发。";
    button.disabled = false;
    button.querySelector("span").textContent = failed ? "重新开始训练" : "开始确定性训练";
    runState.classList.add(failed ? "is-warning" : "is-complete");
    runState.textContent = failed ? "运行失败" : (hasCompletedRun(project) ? "已有结果" : "可训练");
  } else {
    $("#trainGateTitle").textContent = "方案批准后可开始训练";
    $("#trainGateText").textContent = "训练会生成独立 Run ID，并记录方案版本、数据哈希与评估结果。";
    button.disabled = true;
    button.querySelector("span").textContent = "开始确定性训练";
    runState.textContent = "等待批准";
  }

  renderRunResults(project);
}

function renderRunResults(project) {
  const run = getLatestRun(project);
  const result = getRunResult(run);
  const container = $("#runResults");
  if (!result || !result.champion) {
    container.classList.add("is-hidden");
    return;
  }
  container.classList.remove("is-hidden");

  const runId = run.run_id || run.id || result.run_id || "—";
  const reproducibility = result.reproducibility || {};
  const completedAt = run.completed_at || run.updated_at || result.completed_at;
  const meta = [
    `Run ${shortId(runId)}`,
    completedAt ? formatDateTime(completedAt) : null,
    reproducibility.holdout_rows != null ? `留出集 ${formatInteger(reproducibility.holdout_rows)} 行` : null,
    `离线评估`,
  ].filter(Boolean);
  $("#runMeta").textContent = meta.join(" · ");

  const champion = result.champion || {};
  const championId = champion.name || champion.candidate || champion.model || "champion";
  $("#championName").textContent = champion.display_name || modelLabel(championId);
  $("#championReason").textContent = champion.selection_metric
    ? `按 ${metricLabel(champion.selection_metric)} 在训练分区 OOF 上选择`
    : "按方案主指标在训练分区 OOF 上选择";

  const models = normalizeModels(result);
  if (!models.length) {
    $("#modelTabs").innerHTML = "";
    $("#modelResultPanel").innerHTML = '<p class="model-detail-note">当前结果没有候选模型明细。</p>';
  } else {
    const validModelIds = new Set(models.map((model) => model.id));
    if (!state.activeModel || !validModelIds.has(state.activeModel)) {
      state.activeModel = models.find((model) => model.isChampion)?.id || models[0].id;
    }
    $("#modelTabs").innerHTML = models.map((model) => `
      <button class="model-tab" type="button" role="tab" aria-selected="${model.id === state.activeModel}" data-model-id="${escapeHTML(model.id)}">
        ${escapeHTML(model.name)}${model.isChampion ? " · Champion" : ""}
      </button>`).join("");
    const selected = models.find((model) => model.id === state.activeModel) || models[0];
    renderModelMetrics(selected);
  }

  const holdout = result.holdout_metrics || {};
  renderLiftTable(holdout.lift_table || result.lift_table || []);
  const report = $("#reportButton");
  report.href = `/api/projects/${encodeURIComponent(project.id)}/runs/${encodeURIComponent(runId)}/report`;
  report.setAttribute("download", `${safeFilename(project.name || "model-report")}-${safeFilename(shortId(runId))}.html`);
}

function normalizeModels(result) {
  const champion = result.champion || {};
  const championId = String(champion.name || champion.candidate || champion.model || "champion");
  const rows = Array.isArray(result.candidate_comparison)
    ? result.candidate_comparison
    : normalizeObjectModels(result.models || result.candidates || {});
  const models = rows.map((item, index) => {
    const id = String(item.candidate || item.name || item.model || `model-${index + 1}`);
    const isChampion = id === championId;
    return {
      id,
      name: item.display_name || modelLabel(id),
      isChampion,
      metrics: isChampion && result.holdout_metrics
        ? result.holdout_metrics
        : (item.oof_metrics || item.metrics || item),
      scope: isChampion && result.holdout_metrics ? "留出集" : "训练分区 OOF",
    };
  });
  if (!models.some((model) => model.isChampion) && result.holdout_metrics) {
    models.unshift({
      id: championId,
      name: champion.display_name || modelLabel(championId),
      isChampion: true,
      metrics: result.holdout_metrics,
      scope: "留出集",
    });
  }
  return models;
}

function normalizeObjectModels(value) {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== "object") return [];
  return Object.entries(value).map(([name, item]) => ({ name, ...(item || {}) }));
}

function renderModelMetrics(model) {
  const metrics = model.metrics || {};
  const values = [
    ["ROC-AUC", metrics.roc_auc],
    ["KS", metrics.ks],
    ["PR-AUC", metrics.average_precision ?? metrics.pr_auc],
    ["Brier", metrics.brier_score ?? metrics.brier],
    ["F1", metrics.f1 ?? metrics.f1_score],
  ];
  $("#modelResultPanel").innerHTML = `
    <div class="model-metrics">
      ${values.map(([label, value]) => `
        <article class="model-metric"><span>${label}</span><strong>${formatMetric(value)}</strong><small>${escapeHTML(model.scope)}</small></article>
      `).join("")}
    </div>
    <p class="model-detail-note">${escapeHTML(model.scope)}指标由确定性代码计算。候选模型的 OOF 指标与 Champion 的锁定留出集指标属于不同评估范围，不应直接混为同一测试集比较。</p>`;
}

function renderLiftTable(rows) {
  const body = $("#liftTableBody");
  if (!Array.isArray(rows) || !rows.length) {
    body.innerHTML = '<tr><td colspan="6">当前运行没有可用的 Lift 明细。</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row, index) => `<tr>
    <td>${escapeHTML(row.decile ?? row.bin ?? row.group ?? index + 1)}</td>
    <td>${formatInteger(row.count ?? row.samples ?? row.n)}</td>
    <td>${formatInteger(row.positives ?? row.bad_count ?? row.events)}</td>
    <td>${formatPercent(row.positive_rate ?? row.bad_rate ?? row.event_rate)}</td>
    <td>${formatPercent(row.cumulative_capture_rate ?? row.cumulative_bad_capture ?? row.capture_rate)}</td>
    <td>${formatMetric(row.lift)}</td>
  </tr>`).join("");
}

function renderWorkflow(project) {
  const hasProfile = Boolean(project.profile);
  const hasPlan = Boolean(project.plan && project.plan.version);
  const approved = isPlanApproved(project);
  const completed = hasCompletedRun(project);
  const workflow = [
    ["profile", hasProfile, !hasProfile],
    ["plan", hasPlan, hasProfile && !hasPlan],
    ["approval", approved, hasPlan && !approved],
    ["result", completed, approved && !completed],
  ];
  workflow.forEach(([name, complete, current]) => {
    const item = $(`[data-step="${name}"]`);
    item.className = complete ? "is-complete" : (current ? "is-current" : "is-locked");
    const marker = $(".step-state", item);
    marker.setAttribute("aria-label", complete ? "已完成" : (current ? "当前步骤" : "未解锁"));
  });
}

function resetWorkflowSteps() {
  $$("#workflowSteps li").forEach((item) => { item.className = "is-locked"; });
}

async function submitPlan(event) {
  event.preventDefault();
  if (!state.currentProject || state.busy) return;
  const targetColumn = $("#targetColumn").value;
  const positiveLabelRaw = $("#positiveLabel").value.trim();
  if (!targetColumn || !positiveLabelRaw) {
    showToast("方案信息不完整", "请选择目标字段并填写坏样本取值。", "error");
    return;
  }
  const excludedColumns = $$('#excludedColumns input[type="checkbox"]:checked')
    .map((input) => input.value)
    .filter((name) => name !== targetColumn);
  const body = {
    target_column: targetColumn,
    positive_label: coerceLabel(positiveLabelRaw),
    time_column: $("#timeColumn").value || null,
    excluded_columns: excludedColumns,
  };

  state.busy = true;
  setLoading(true, "正在生成可审阅方案…");
  try {
    const payload = await apiRequest(`/api/projects/${encodeURIComponent(state.currentProject.id)}/plan`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    await ensureProjectFromResponse(payload);
    const blockers = getBlockingIssues(state.currentProject);
    showToast(
      blockers.length ? "方案已生成，但存在阻断项" : "方案已生成",
      blockers.length ? "请先核验并解决阻断问题，再批准方案。" : "请逐项完成人工确认。",
      blockers.length ? "error" : "success",
    );
    $("#planOutput").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    showToast("方案生成失败", error.message, "error");
  } finally {
    state.busy = false;
    setLoading(false);
  }
}

async function approvePlan() {
  const project = state.currentProject;
  const plan = project && project.plan;
  if (!project || !plan || state.busy) return;
  const confirmations = $$('#confirmationList input[type="checkbox"]:checked').map((input) => input.value);
  const body = {
    plan_version: plan.version,
    plan_hash: plan.plan_hash,
    confirmations,
  };
  state.busy = true;
  setLoading(true, "正在绑定人工确认记录…");
  try {
    const payload = await apiRequest(`/api/projects/${encodeURIComponent(project.id)}/approve`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    await ensureProjectFromResponse(payload);
    showToast("方案已批准", "训练已解锁；系统不会自动触发运行。", "success");
  } catch (error) {
    showToast("方案批准失败", error.message, "error");
  } finally {
    state.busy = false;
    setLoading(false);
  }
}

async function trainModel() {
  const project = state.currentProject;
  if (!project || state.busy) return;
  state.busy = true;
  setLoading(true, "正在创建确定性训练运行…");
  try {
    const payload = await apiRequest(`/api/projects/${encodeURIComponent(project.id)}/train`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await ensureProjectFromResponse(payload);
    showToast("训练已启动", "本次运行使用已批准方案，并生成独立 Run ID。", "success");
  } catch (error) {
    showToast("训练启动失败", error.message, "error");
    return;
  } finally {
    state.busy = false;
    setLoading(false);
  }

  if (state.currentProject && state.currentProject.status === "training") {
    pollTrainingResult();
  }
}

async function pollTrainingResult() {
  if (state.polling || !state.currentProject) return;
  state.polling = true;
  const projectId = state.currentProject.id;
  try {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      await wait(1250);
      if (!state.currentProject || state.currentProject.id !== projectId) break;
      await refreshCurrentProject(false);
      if (state.currentProject.status !== "training") {
        if (hasCompletedRun(state.currentProject)) {
          showToast("训练完成", "离线指标与报告已经生成。", "success");
          $("#resultSection").scrollIntoView({ behavior: "smooth", block: "start" });
        } else if (state.currentProject.status === "failed") {
          showToast("训练失败", "请查看项目状态和审计事件后再决定是否重试。", "error");
        }
        break;
      }
    }
  } catch (error) {
    showToast("训练状态刷新失败", error.message, "error");
  } finally {
    state.polling = false;
  }
}

async function sendAgentMessage(event) {
  event.preventDefault();
  const input = $("#agentInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  appendMessage("user", message);

  if (!state.currentProject) {
    appendMessage("agent", "请先上传一份 CSV 或载入演示数据。我只能解释当前项目已经产生的本地证据。");
    return;
  }

  const loadingMessage = appendLoadingMessage();
  $("#agentSendButton").disabled = true;
  try {
    const payload = await apiRequest(`/api/projects/${encodeURIComponent(state.currentProject.id)}/agent`, {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    loadingMessage.remove();
    appendMessage("agent", agentResponseText(payload));
  } catch (error) {
    loadingMessage.remove();
    appendMessage("agent", `当前无法读取本地证据：${error.message}`);
  } finally {
    $("#agentSendButton").disabled = false;
    input.focus();
  }
}

function agentResponseText(payload) {
  const response = payload && (payload.response || payload.reply || payload);
  if (typeof response === "string") return response;
  if (!response || typeof response !== "object") return "没有可用的解释结果。";
  const sections = Array.isArray(response.sections) ? response.sections : [];
  const kindLabels = { fact: "事实", risk: "风险", suggestion: "建议" };
  const lines = sections.map((section) => {
    if (typeof section === "string") return section;
    const prefix = kindLabels[section.kind] ? `【${kindLabels[section.kind]}】` : "";
    return `${prefix}${section.text || section.message || ""}`;
  }).filter(Boolean);
  if (response.message) lines.unshift(response.message);
  if (response.boundary) lines.push(`【边界】${response.boundary}`);
  return lines.join("\n\n") || "已读取当前项目，但没有匹配到可解释的本地产物。";
}

function appendMessage(role, text) {
  const article = document.createElement("article");
  article.className = `message message--${role}`;
  const roleLabel = document.createElement("span");
  roleLabel.className = "message-role";
  roleLabel.textContent = role === "user" ? "你" : "规则化助手";
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  article.append(roleLabel, paragraph);
  $("#messageList").append(article);
  $("#messageList").scrollTop = $("#messageList").scrollHeight;
  return article;
}

function appendLoadingMessage() {
  const article = document.createElement("article");
  article.className = "message message--agent message--loading";
  article.innerHTML = '<span class="message-role">规则化助手</span><p><span class="typing-dots" aria-label="正在读取本地证据"><i></i><i></i><i></i></span></p>';
  $("#messageList").append(article);
  $("#messageList").scrollTop = $("#messageList").scrollHeight;
  return article;
}

function getColumnEntries(project) {
  const profileColumns = project && project.profile && project.profile.columns;
  if (profileColumns && !Array.isArray(profileColumns) && typeof profileColumns === "object") {
    return Object.entries(profileColumns);
  }
  if (Array.isArray(profileColumns)) {
    return profileColumns.map((item) => typeof item === "string" ? [item, {}] : [item.name || item.column, item]);
  }
  const datasetColumns = project && project.dataset && project.dataset.columns;
  if (Array.isArray(datasetColumns)) {
    return datasetColumns.map((item) => typeof item === "string" ? [item, {}] : [item.name || item.column, item]);
  }
  return [];
}

function getBlockingIssues(project) {
  const profile = project.profile || {};
  const plan = project.plan || {};
  return deduplicateIssues([
    ...(Array.isArray(profile.blocking_issues) ? profile.blocking_issues : []),
    ...(Array.isArray(plan.blocking_issues) ? plan.blocking_issues : []),
  ]);
}

function getWarnings(project) {
  const profile = project.profile || {};
  const plan = project.plan || {};
  return deduplicateIssues([
    ...(Array.isArray(profile.warnings) ? profile.warnings : []),
    ...(Array.isArray(plan.warnings) ? plan.warnings : []),
  ]);
}

function deduplicateIssues(issues) {
  const seen = new Set();
  return issues.filter((issue) => {
    const key = typeof issue === "string"
      ? issue
      : `${issue.code || ""}:${(issue.columns || []).join(",")}:${issue.message || ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function issueText(issue) {
  if (typeof issue === "string") return issue;
  const base = ISSUE_LABELS[issue.code] || issue.message || issue.code || "未命名问题";
  const columns = Array.isArray(issue.columns) && issue.columns.length
    ? `字段：${issue.columns.slice(0, 4).join("、")}${issue.columns.length > 4 ? " 等" : ""}`
    : "";
  return columns ? `${base} ${columns}` : base;
}

function getLatestRun(project) {
  if (project.latest_run) return project.latest_run;
  const runs = Array.isArray(project.runs) ? project.runs : [];
  return runs.length ? runs[runs.length - 1] : {};
}

function getRunResult(run) {
  if (!run || typeof run !== "object") return null;
  if (run.result && typeof run.result === "object") return run.result;
  if (run.holdout_metrics || run.champion) return run;
  return null;
}

function hasCompletedRun(project) {
  const result = getRunResult(getLatestRun(project));
  return Boolean(result && result.champion);
}

function isPlanApproved(project) {
  const plan = project.plan || {};
  const approval = project.approval || plan.approval || {};
  if (approval.approved === true) return !approval.plan_hash || approval.plan_hash === plan.plan_hash;
  if (approval.plan_hash && approval.plan_hash === plan.plan_hash) return true;
  return ["approved", "training", "completed"].includes(project.status) && Boolean(plan.version);
}

function isDemoProject(project) {
  const dataset = project.dataset || {};
  const filename = String(dataset.original_filename || dataset.filename || "").toLowerCase();
  const name = String(project.name || "").toLowerCase();
  return Boolean(project.dataset_is_demo || dataset.is_demo || filename.includes("sample") || filename.includes("demo") || name.includes("演示"));
}

function syncProtectedColumns() {
  const target = $("#targetColumn").value;
  const time = $("#timeColumn").value;
  $$('#excludedColumns input[type="checkbox"]').forEach((input) => {
    const protectedTarget = input.value === target;
    input.disabled = protectedTarget;
    if (protectedTarget) input.checked = false;
    const chip = input.closest(".checkbox-chip");
    if (chip) chip.title = protectedTarget ? "目标字段不能同时作为排除字段" : (input.value === time ? "时间字段会用于切分，不会作为训练特征" : "");
  });
  updateExcludedCount();
}

function updateExcludedCount() {
  const count = $$('#excludedColumns input[type="checkbox"]:checked').length;
  $("#excludedCount").textContent = `已排除 ${count} 个`;
}

function filterColumnChips() {
  const query = $("#columnSearch").value.trim().toLowerCase();
  $$("#excludedColumns .checkbox-chip").forEach((chip) => {
    chip.classList.toggle("is-filtered", Boolean(query) && !chip.dataset.columnName.includes(query));
  });
}

function updateTargetValueHints() {
  const project = state.currentProject || {};
  const target = $("#targetColumn").value;
  const profile = project.profile || {};
  const column = profile.columns && profile.columns[target];
  const candidate = Array.isArray(profile.binary_candidates)
    ? profile.binary_candidates.find((item) => item.column === target)
    : null;
  const values = (candidate && candidate.values)
    || (column && (column.values || column.unique_values || column.top_values));
  const normalized = Array.isArray(values)
    ? values.map((item) => typeof item === "object" ? (item.value ?? item.label) : item)
    : [];
  $("#positiveLabelOptions").innerHTML = normalized
    .filter((value) => value != null)
    .slice(0, 20)
    .map((value) => `<option value="${escapeHTML(value)}"></option>`)
    .join("");
}

function updateSelectedFile() {
  const file = $("#datasetFile").files[0];
  const dropZone = $("#dropZone");
  if (!file) {
    resetFilePicker();
    return;
  }
  dropZone.classList.add("has-file");
  $("#filePrompt").textContent = file.name;
  $("#fileHint").textContent = `${formatBytes(file.size)} · 点击可重新选择`;
  if (!$("#projectName").value.trim()) {
    $("#projectName").value = file.name.replace(/\.csv$/i, "").slice(0, 80);
  }
}

function resetFilePicker() {
  $("#dropZone").classList.remove("has-file", "is-dragging");
  $("#filePrompt").textContent = "选择或拖入 CSV 文件";
  $("#fileHint").textContent = "一行一条申请记录；原始数据仅在本机处理";
}

function toggleAgentPanel() {
  const panel = $(".agent-panel");
  const button = $("#agentCollapseButton");
  const collapsed = panel.classList.toggle("is-collapsed");
  button.setAttribute("aria-expanded", String(!collapsed));
  button.textContent = collapsed ? "+" : "−";
  button.title = collapsed ? "展开助手" : "收起助手";
}

function toggleMobileNav() {
  const sidebar = $("#projectSidebar");
  const button = $("#mobileNavButton");
  const open = sidebar.classList.toggle("is-open");
  button.setAttribute("aria-expanded", String(open));
}

function closeMobileNav() {
  $("#projectSidebar").classList.remove("is-open");
  $("#mobileNavButton").setAttribute("aria-expanded", "false");
}

function setLoading(visible, text = "处理中…") {
  $("#loadingText").textContent = text;
  $("#loadingOverlay").classList.toggle("is-hidden", !visible);
}

function showToast(title, message, type = "info") {
  const toast = document.createElement("div");
  toast.className = `toast toast--${type}`;
  const mark = type === "success" ? "✓" : (type === "error" ? "!" : "i");
  toast.innerHTML = `<b aria-hidden="true">${mark}</b><div><strong>${escapeHTML(title)}</strong><span>${escapeHTML(message)}</span></div>`;
  $("#toastRegion").append(toast);
  window.setTimeout(() => toast.remove(), 5200);
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status || "状态未知";
}

function typeLabel(type) {
  return TYPE_LABELS[type] || type || TYPE_LABELS.unknown;
}

function modelLabel(name) {
  return MODEL_LABELS[name] || String(name || "未命名模型").replaceAll("_", " ");
}

function splitLabel(method) {
  if (method === "time_holdout" || method === "time") return "按时间留出";
  if (method === "stratified_random") return "分层随机留出";
  return method || "—";
}

function metricLabel(name) {
  const labels = {
    roc_auc: "ROC-AUC",
    oof_roc_auc: "OOF ROC-AUC",
    ks: "KS",
    oof_ks: "OOF KS",
    average_precision: "PR-AUC",
    pr_auc: "PR-AUC",
    brier_score: "Brier Score",
  };
  return labels[name] || String(name || "—").replaceAll("_", " ");
}

function coerceLabel(value) {
  if (/^-?(?:\d+\.?\d*|\.\d+)$/.test(value)) return Number(value);
  if (value.toLowerCase() === "true") return true;
  if (value.toLowerCase() === "false") return false;
  return value;
}

function formatInteger(value) {
  if (value == null || value === "" || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value));
}

function formatMetric(value) {
  if (value == null || value === "" || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(4);
}

function formatPercent(value) {
  if (value == null || value === "" || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return `${(number <= 1 ? number * 100 : number).toFixed(1)}%`;
}

function formatBytes(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  if (number < 1024) return `${number} B`;
  if (number < 1024 * 1024) return `${(number / 1024).toFixed(1)} KB`;
  return `${(number / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function shortId(value) {
  const text = String(value || "—");
  return text.length > 16 ? `${text.slice(0, 8)}…${text.slice(-5)}` : text;
}

function safeFilename(value) {
  return String(value).replace(/[^\w\u4e00-\u9fff.-]+/g, "-").replace(/^-+|-+$/g, "") || "report";
}

function escapeHTML(value) {
  return String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
