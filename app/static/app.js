const state = { projects: [], project: null, datasets: [], selectedDatasetId: null, dictionaries: [], runs: [], run: null, events: [], conversation: null, messages: [], mode: 'auto', defaultMode: 'auto', activeWorkspaceTab: 'overview', lastAutoPhase: null, featurePage: 1, selectionPage: 1, eventSource: null, config: null, providerPresets: {}, providerRequests: [], confirmationRunId: null, confirmationFeatures: [], confirmationExcluded: new Set(), selectionExcluded: new Set(), settingsReturnFocus: null };
const $ = function (selector) { return document.querySelector(selector); };
const projectList = $('#project-list');
const escapeHtml = function (value) { return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[char]; }); };
let dialogResolver = null;
let dialogReturnFocus = null;
let dialogKeyHandler = null;

async function api(path, options) {
  const response = await fetch(path, options || {});
  const payload = await response.json().catch(function () { return {}; });
  if (!response.ok) { const detail = payload.detail; throw new Error(typeof detail === 'string' ? detail : detail && detail.message ? detail.message + (detail.sheets ? ' 可选 Sheet：' + detail.sheets.join('、') : '') : payload.message || '请求失败'); }
  return payload;
}
function showNotice(message, kind) {
  const notice = $('#notice');
  notice.textContent = message;
  notice.className = 'notice' + (kind ? ' ' + kind : '');
  notice.classList.remove('hidden');
}
function closeDialog(value) {
  const dialog = $('#interaction-dialog');
  if (!dialog) return;
  dialog.classList.add('hidden');
  if (dialogKeyHandler) dialog.removeEventListener('keydown', dialogKeyHandler);
  dialogKeyHandler = null;
  const resolver = dialogResolver;
  dialogResolver = null;
  if (resolver) resolver(value);
  if (dialogReturnFocus && document.contains(dialogReturnFocus)) window.requestAnimationFrame(function () { dialogReturnFocus.focus({ preventScroll: true }); });
  dialogReturnFocus = null;
}
function dialogFieldMarkup(field) {
  const name = escapeHtml(field.name || 'value'); const label = escapeHtml(field.label || '输入'); const value = escapeHtml(field.value == null ? '' : field.value); const required = field.required ? ' required' : '';
  if (field.type === 'select') {
    const options = (field.options || []).map(function (option) { const item = typeof option === 'string' ? { value: option, label: option } : option; return '<option value="' + escapeHtml(item.value) + '" ' + (String(item.value) === String(field.value) ? 'selected' : '') + '>' + escapeHtml(item.label) + '</option>'; }).join('');
    return '<label class="dialog-field"><span>' + label + '</span><select name="' + name + '"' + required + '>' + options + '</select></label>';
  }
  if (field.type === 'range') {
    const min = escapeHtml(field.min == null ? 0 : field.min); const max = escapeHtml(field.max == null ? 1 : field.max); const step = escapeHtml(field.step == null ? 0.001 : field.step); const output = escapeHtml(field.value == null ? '' : field.value);
    return '<label class="dialog-field"><span>' + label + '</span><input name="' + name + '" type="range" min="' + min + '" max="' + max + '" step="' + step + '" value="' + value + '"' + required + ' /><span class="dialog-range-meta"><span>' + min + '</span><output data-dialog-output="' + name + '">' + output + '</output><span>' + max + '</span></span></label>';
  }
  const type = field.type === 'textarea' ? 'textarea' : 'input';
  if (type === 'textarea') return '<label class="dialog-field"><span>' + label + '</span><textarea name="' + name + '" maxlength="' + escapeHtml(field.maxlength || 2000) + '"' + required + '>' + value + '</textarea></label>';
  const min = field.min == null ? '' : ' min="' + escapeHtml(field.min) + '"'; const max = field.max == null ? '' : ' max="' + escapeHtml(field.max) + '"'; const step = field.step == null ? '' : ' step="' + escapeHtml(field.step) + '"'; const maxlength = field.maxlength == null ? '' : ' maxlength="' + escapeHtml(field.maxlength) + '"';
  return '<label class="dialog-field"><span>' + label + '</span><input name="' + name + '" type="' + escapeHtml(field.type || 'text') + '" value="' + value + '" placeholder="' + escapeHtml(field.placeholder || '') + '"' + min + max + step + maxlength + (field.autocomplete ? ' autocomplete="' + escapeHtml(field.autocomplete) + '"' : '') + required + ' /></label>';
}
function openDialog(options) {
  const dialog = $('#interaction-dialog');
  if (!dialog) return Promise.resolve(null);
  if (dialogResolver) closeDialog(null);
  return new Promise(function (resolve) {
    dialogResolver = resolve; dialogReturnFocus = document.activeElement;
    $('#interaction-dialog-title').textContent = options.title || '确认操作';
    $('#interaction-dialog-message').textContent = options.message || '';
    const fields = options.fields || [];
    $('#interaction-dialog-fields').innerHTML = fields.map(dialogFieldMarkup).join('');
    $('#interaction-dialog-submit').textContent = options.submitLabel || (options.confirm ? '确认' : '继续');
    $('#interaction-dialog-cancel').textContent = options.cancelLabel || '取消';
    dialog.classList.remove('hidden');
    const card = dialog.querySelector('.modal-card');
    dialogKeyHandler = function (event) {
      if (event.key === 'Escape') { event.preventDefault(); closeDialog(null); }
      else if (event.key === 'Enter' && event.target.tagName !== 'TEXTAREA' && !event.isComposing) { event.preventDefault(); $('#interaction-dialog-submit').click(); }
    };
    dialog.addEventListener('keydown', dialogKeyHandler);
    dialog.querySelectorAll('input[type="range"]').forEach(function (input) { input.addEventListener('input', function () { const output = dialog.querySelector('[data-dialog-output="' + input.name + '"]'); if (output) output.value = input.value; }); });
    const first = dialog.querySelector('#interaction-dialog-fields input, #interaction-dialog-fields select, #interaction-dialog-fields textarea') || $('#interaction-dialog-submit'); window.requestAnimationFrame(function () { (first || card).focus(); });
  });
}
function readDialogFields() {
  const fields = {}; $('#interaction-dialog-fields').querySelectorAll('[name]').forEach(function (input) { fields[input.name] = input.value; });
  return fields;
}
function setAppView(view, moveFocus) {
  const settingsOpen = view === 'settings';
  $('#project-view').classList.toggle('hidden', settingsOpen);
  $('#settings-view').classList.toggle('hidden', !settingsOpen);
  $('#open-workspace').classList.toggle('active', !settingsOpen);
  $('#open-settings').classList.toggle('active', settingsOpen);
  window.scrollTo({ top: 0, behavior: 'auto' });
  if (moveFocus) window.requestAnimationFrame(function () {
    const fallback = $('#open-workspace');
    const target = settingsOpen ? $('#settings-title') : (state.settingsReturnFocus && document.contains(state.settingsReturnFocus) ? state.settingsReturnFocus : fallback);
    if (target) target.focus({ preventScroll: true });
    if (!settingsOpen) state.settingsReturnFocus = null;
  });
}
function setWorkspaceTab(tab) {
  const target = document.querySelector('[data-workspace-panel="' + tab + '"]');
  if (!target) return;
  state.activeWorkspaceTab = tab;
  document.querySelectorAll('[data-workspace-tab]').forEach(function (button) { const selected = button.dataset.workspaceTab === tab; button.classList.toggle('active', selected); button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1; });
  document.querySelectorAll('[data-workspace-panel]').forEach(function (panel) { const selected = panel.dataset.workspacePanel === tab; panel.classList.toggle('active', selected); panel.setAttribute('aria-hidden', String(!selected)); });
}
const phaseWorkspaceTabs = { profiling: 'data', planning: 'overview', eda: 'data', cleaning: 'data', screening: 'data', training: 'execution', reporting: 'report' };
function autoFocusRunTab(run) {
  const phase = run && run.phase;
  const tab = phaseWorkspaceTabs[phase];
  if (!tab || state.lastAutoPhase === phase) return;
  state.lastAutoPhase = phase;
  setWorkspaceTab(tab);
}
function runIsActive() {
  return Boolean(state.run && ['queued', 'running', 'paused', 'awaiting_confirmation'].includes(state.run.status));
}
function modeLabel(mode) { return mode === 'semi_trust' ? '半信任模式' : '完全信任模式'; }
function renderModeIndicator() {
  const active = runIsActive();
  const mode = active ? state.run.mode : state.defaultMode;
  $('#run-mode-chip').textContent = (active ? '本次：' : '默认：') + modeLabel(mode).replace('模式', '');
  $('#run-mode').textContent = modeLabel(state.run ? state.run.mode : state.defaultMode);
}
function renderProjects() {
  projectList.innerHTML = state.projects.length ? state.projects.map(function (project) {
    return '<button class="project-item ' + (state.project && state.project.id === project.id ? 'active' : '') + '" data-project-id="' + escapeHtml(project.id) + '"><strong>' + escapeHtml(project.name) + '</strong><small>' + escapeHtml(project.status) + '</small></button>';
  }).join('') : '<div class="muted" style="padding:10px">还没有项目</div>';
  projectList.querySelectorAll('[data-project-id]').forEach(function (button) { button.addEventListener('click', function () { setAppView('project'); selectProject(button.dataset.projectId); }); });
}
async function loadProjects() {
  const data = await api('/api/projects');
  state.projects = data.projects || [];
  renderProjects();
  if (state.projects.length && !state.project) await selectProject(state.projects[0].id);
  if (!state.projects.length) { $('#empty-state').classList.remove('hidden'); $('#workspace').classList.add('hidden'); }
}
async function selectProject(projectId) {
  const data = await api('/api/projects/' + projectId);
  state.project = data.project; state.datasets = data.datasets || []; state.dictionaries = data.dictionaries || []; state.runs = data.runs || []; state.run = state.runs[0] || null; state.selectedDatasetId = (state.run && state.run.dataset_id) || (state.datasets[0] && state.datasets[0].id) || null; state.mode = runIsActive() ? state.run.mode : state.defaultMode; state.events = []; state.providerRequests = []; state.selectionExcluded = new Set(); state.lastAutoPhase = null; state.featurePage = 1; state.selectionPage = 1;
  setWorkspaceTab('overview');
  $('#report-panel').classList.add('hidden'); $('#report-empty-state').classList.remove('hidden'); $('#report-tab').classList.remove('has-result');
  await loadConversation();
  renderProjects(); renderProject();
  await loadProviderRequests();
  if (state.run) {
    const eventData = await api('/api/runs/' + state.run.id + '/events');
    state.events = eventData.events || []; renderTimeline(); connectStream();
    if (state.run.status === 'succeeded') {
      try { renderReport(await api('/api/runs/' + state.run.id + '/report')); } catch (error) { /* report may still be flushing */ }
    }
  }
}
function activeDataset() {
  const runLocksDataset = state.run && ['queued', 'running', 'paused', 'awaiting_confirmation'].includes(state.run.status);
  const preferred = runLocksDataset && state.run.dataset_id ? state.run.dataset_id : (state.selectedDatasetId || (state.run && state.run.dataset_id));
  return state.datasets.find(function (item) { return item.id === preferred; }) || state.datasets[0] || null;
}
function activeProfile() {
  const dataset = activeDataset();
  if (state.run && dataset && state.run.dataset_id === dataset.id && state.run.state && state.run.state.profile) return state.run.state.profile;
  return (dataset && dataset.profile) || {};
}
async function loadProviderRequests() {
  const block = $('#provider-audit-block');
  if (!block) return;
  if (!state.run) { block.classList.add('hidden'); return; }
  block.classList.remove('hidden');
  try {
    const data = await api('/api/runs/' + state.run.id + '/provider-requests');
    state.providerRequests = data.requests || [];
  } catch (error) { state.providerRequests = []; }
  renderProviderRequests();
}
function renderProviderRequests() {
  const count = $('#provider-request-count'); const list = $('#provider-request-list');
  if (!count || !list) return;
  count.textContent = state.providerRequests.length + ' 次';
  if (!state.providerRequests.length) { list.innerHTML = '<span class="muted">当前 Run 尚无外部请求。</span>'; return; }
  list.innerHTML = state.providerRequests.slice().reverse().slice(0, 12).map(function (item) {
    const summary = item.payload_summary || item.payload || {};
    const safe = JSON.stringify(summary, null, 2);
    return '<details class="provider-request"><summary><strong>' + escapeHtml(item.purpose || 'provider') + '</strong><span>' + escapeHtml(item.model || '—') + '</span><small>' + escapeHtml((item.created_at || '').slice(11, 19)) + '</small></summary><pre>' + escapeHtml(safe) + '</pre></details>';
  }).join('');
}
async function loadConversation() {
  if (!state.project) return;
  const suffix = state.run ? '?run_id=' + encodeURIComponent(state.run.id) : '';
  const data = await api('/api/projects/' + state.project.id + '/conversation' + suffix);
  state.conversation = data.conversation; state.messages = data.messages || [];
  renderConversation();
}
function renderProject() {
  const hasProject = Boolean(state.project);
  $('#empty-state').classList.toggle('hidden', hasProject); $('#workspace').classList.toggle('hidden', !hasProject);
  const archiveButton = $('#archive-project'); const deleteButton = $('#delete-project');
  if (archiveButton) { archiveButton.hidden = !hasProject; archiveButton.textContent = hasProject && state.project.status === 'archived' ? '恢复项目' : '归档项目'; archiveButton.disabled = Boolean(state.run && ['queued', 'running', 'paused', 'awaiting_confirmation'].includes(state.run.status)); }
  if (deleteButton) { deleteButton.hidden = !hasProject; deleteButton.disabled = Boolean(state.run && ['queued', 'running', 'paused', 'awaiting_confirmation'].includes(state.run.status)); }
  if (!hasProject) return;
  $('#project-title').textContent = state.project.name;
  $('#dataset-version').textContent = state.datasets.length ? 'v' + state.datasets.length : '—';
  $('#run-version').textContent = state.run ? state.run.id.slice(-6) : '—';
  renderDataset(); renderDictionaryStatus(); renderRun(); renderStages(); renderAnalysisControls(); renderConversation();
  $('#start-run').disabled = state.project.status === 'archived' || !state.datasets.length || ['queued', 'running', 'paused', 'awaiting_confirmation'].includes(state.run && state.run.status);
}
function renderDataset() {
  const dataset = activeDataset(); const card = $('#dataset-card');
  if (!dataset) {
    card.className = 'dataset-card empty-panel';
    card.innerHTML = '<div class="drop-icon">↑</div><strong>拖入 CSV / XLSX</strong><span>文件只写入本机项目目录</span><button id="choose-file" class="secondary-button small">选择文件</button>';
    $('#dataset-badge').textContent = '未导入'; $('#choose-file').addEventListener('click', function () { $('#file-input').click(); }); return;
  }
  card.className = 'dataset-card dataset-filled';
  const resource = dataset.profile && dataset.profile.resource_estimate || {};
  const resourceNote = resource.risk === 'warn' ? '<br/><span class="warn-text">资源预估接近本机预算</span>' : resource.risk === 'block' ? '<br/><span class="error-text">资源预估超过当前边界</span>' : '';
  const runActive = state.run && ['queued', 'running', 'paused', 'awaiting_confirmation'].includes(state.run.status);
  const datasetOptions = state.datasets.length > 1 ? '<label class="dataset-picker"><span>当前数据版本</span><select id="dataset-select"' + (runActive ? ' disabled' : '') + '>' + state.datasets.map(function (item) { return '<option value="' + escapeHtml(item.id) + '" ' + (item.id === dataset.id ? 'selected' : '') + '>' + escapeHtml(item.filename) + (item.is_demo ? ' · 演示' : '') + '</option>'; }).join('') + '</select></label>' : '';
  card.innerHTML = '<div class="file-title"><span class="drop-icon">✓</span>' + escapeHtml(dataset.filename) + '</div><div class="file-meta">大小 ' + (dataset.bytes / 1024).toFixed(1) + ' KB<br/>' + (dataset.rows || '—') + ' 行 · ' + (dataset.columns || '—') + ' 列<br/>SHA-256 ' + escapeHtml(dataset.sha256.slice(0, 16)) + '…' + resourceNote + '</div>' + datasetOptions + '<button id="replace-file" class="secondary-button small" type="button">重新导入版本</button>';
  $('#replace-file').addEventListener('click', function () { $('#file-input').click(); });
  if ($('#dataset-select')) $('#dataset-select').addEventListener('change', function () { state.selectedDatasetId = $('#dataset-select').value; renderProject(); });
  $('#dataset-badge').textContent = dataset.is_demo ? '合成演示' : '本地已导入';
  $('#dataset-badge').className = 'chip ' + (dataset.is_demo ? 'warn' : 'safe');
}
function renderDictionaryStatus() {
  const status = $('#dictionary-status');
  if (!status) return;
  const dictionary = state.dictionaries[0];
  status.textContent = dictionary ? '已绑定：' + dictionary.filename + ' · ' + ((dictionary.metadata || {}).field_count || 0) + ' 个字段语义' : '字段中文名、口径和来源会留在本机并进入报告。';
}
function phaseLabel(phase) { return ({ profiling: '数据画像', planning: '计划与审核', eda: '探索分析', cleaning: '数据清洗', screening: '变量筛选', training: '模型训练', reporting: '报告产出' })[phase] || phase || '等待'; }
function renderRun() {
  const run = state.run; const status = run && run.status || 'idle'; const event = state.events[state.events.length - 1];
  autoFocusRunTab(run);
  $('#current-node').textContent = event && event.payload && event.payload.node ? phaseLabel(event.payload.node) : (status === 'idle' ? '等待导入数据' : phaseLabel(run.phase));
  $('#current-message').textContent = event && event.payload && event.payload.message ? event.payload.message : statusText(status);
  $('#inspector-status').textContent = statusLabel(status); $('#inspector-status').className = 'chip ' + statusClass(status);
  renderModeIndicator();
  const progress = event && event.payload && event.payload.progress || (status === 'succeeded' ? 100 : 0);
  $('#progress-value').textContent = progress + '%'; $('.progress-ring').style.setProperty('--progress', progress + '%');
  $('#progress-label').textContent = status === 'succeeded' ? '已完成' : status === 'awaiting_confirmation' ? '等待确认' : status === 'failed' ? '需要处理' : status === 'idle' ? '尚未开始' : '运行中';
  renderActionCard();
}
function statusText(status) { return ({ idle: '先选择自己的文件，或者用演示数据启动本地流程。', queued: 'Run 已排队，等待本地 Worker。', running: '本地 Worker 正在处理。', paused: 'Run 已暂停，已保存到最近安全节点。', awaiting_confirmation: '计划已生成，等待你确认关键业务决定。', succeeded: '报告和模型比较已保存到本机。', failed: '运行失败，旧产物不会被当作新结果。', blocked: '安全或数据契约阻断，训练尚未启动。', cancelled: 'Run 已取消，未完成产物不会被当作正式结果。' })[status] || '等待下一步'; }
function statusLabel(status) { return ({ idle: '等待', queued: '排队', running: '运行中', paused: '已暂停', awaiting_confirmation: '待确认', succeeded: '已完成', failed: '失败', blocked: '已阻断', cancelled: '已取消' })[status] || status; }
function statusClass(status) { return ({ running: 'running', queued: 'running', awaiting_confirmation: 'warn', paused: 'warn', failed: 'block', blocked: 'block', cancelled: 'block', succeeded: 'safe' })[status] || 'neutral'; }
function renderStages() {
  const current = state.run && state.run.phase; const phases = [['profiling', '数据画像'], ['planning', 'Y 与计划'], ['eda', '探索分析'], ['cleaning', '数据清洗'], ['screening', '变量筛选'], ['training', '模型训练'], ['reporting', '报告与产物']]; const order = phases.map(function (item) { return item[0]; }); const currentIndex = order.indexOf(current);
  $('#stage-list').innerHTML = phases.map(function (item, index) {
    const done = state.run && (state.run.status === 'succeeded' || (currentIndex >= 0 && index < currentIndex)); const active = current === item[0]; const blocked = state.run && state.run.status === 'blocked' && active;
    return '<button type="button" class="stage-item ' + (done ? 'done' : '') + ' ' + (active ? 'active' : '') + ' ' + (blocked ? 'blocked' : '') + '" data-stage-tab="' + phaseWorkspaceTabs[item[0]] + '">' + (done ? '✓ ' : '') + item[1] + '</button>';
  }).join('');
  $('#stage-list').querySelectorAll('[data-stage-tab]').forEach(function (button) { button.addEventListener('click', function () { setWorkspaceTab(button.dataset.stageTab); }); });
}
function renderAnalysisControls() {
  const control = $('#analysis-controls'); const button = $('#run-analysis');
  if (!control || !button) return;
  const profile = activeProfile();
  const columns = profile.columns_detail || [];
  if (!columns.length) { control.innerHTML = '<span class="muted">开始一次分析后，这里会出现可选字段。</span>'; button.disabled = true; return; }
  const options = columns.map(function (item) { return '<option value="' + escapeHtml(item.name) + '">' + escapeHtml(item.name) + ' · ' + escapeHtml(item.type || '') + '</option>'; }).join('');
  control.innerHTML = [0, 1, 2, 3].map(function (index) { return '<select class="analysis-select" data-analysis-index="' + index + '"><option value="">维度 ' + (index + 1) + '（可选）</option>' + options + '</select>'; }).join('');
  control.querySelectorAll('.analysis-select').forEach(function (select) { select.addEventListener('change', function () { button.disabled = !Array.from(control.querySelectorAll('.analysis-select')).some(function (item) { return item.value; }); }); });
  button.disabled = true;
}
function renderFeatureSelection() {
  const list = $('#feature-list'); const search = $('#feature-search'); const count = $('#feature-count');
  if (!list || !search || !count) return;
  const query = search.value.trim().toLowerCase();
  const matching = state.confirmationFeatures.filter(function (item) {
    const label = ((item.name || '') + ' ' + (((item.dictionary || {}).display_name) || '')).toLowerCase();
    return !query || label.includes(query);
  });
  const pageSize = 50; const pageCount = Math.max(1, Math.ceil(matching.length / pageSize)); state.featurePage = Math.min(Math.max(state.featurePage || 1, 1), pageCount); const start = (state.featurePage - 1) * pageSize; const visible = matching.slice(start, start + pageSize);
  const pageControls = pageCount > 1 ? '<div class="table-pagination"><button type="button" class="secondary-button small" id="feature-page-prev"' + (state.featurePage === 1 ? ' disabled' : '') + '>上一页</button><span>第 ' + state.featurePage + ' / ' + pageCount + ' 页</span><button type="button" class="secondary-button small" id="feature-page-next"' + (state.featurePage === pageCount ? ' disabled' : '') + '>下一页</button></div>' : '';
  list.innerHTML = (visible.length ? visible.map(function (item) {
    const dictionary = item.dictionary || {};
    const display = dictionary.display_name && dictionary.display_name !== item.name ? ' · ' + dictionary.display_name : '';
    const missing = item.missing_rate == null ? '缺失 —' : '缺失 ' + (Number(item.missing_rate) * 100).toFixed(1) + '%';
    return '<label class="feature-option"><input type="checkbox" data-confirm-exclude="' + escapeHtml(item.name) + '" ' + (state.confirmationExcluded.has(item.name) ? 'checked' : '') + ' /><span><strong>' + escapeHtml(item.name) + '</strong><small>' + escapeHtml((item.type || 'unknown') + display + ' · ' + missing) + '</small></span></label>';
  }).join('') : '<span class="muted">没有匹配字段。</span>') + pageControls;
  count.textContent = (query ? '匹配 ' + matching.length + ' 个' : '可选 ' + state.confirmationFeatures.length + ' 个') + (pageCount > 1 ? ' · 第 ' + state.featurePage + ' / ' + pageCount + ' 页' : '');
  list.querySelectorAll('[data-confirm-exclude]').forEach(function (input) {
    input.addEventListener('change', function () {
      if (input.checked) state.confirmationExcluded.add(input.dataset.confirmExclude);
      else state.confirmationExcluded.delete(input.dataset.confirmExclude);
    });
  });
  if ($('#feature-page-prev')) $('#feature-page-prev').addEventListener('click', function () { state.featurePage -= 1; renderFeatureSelection(); });
  if ($('#feature-page-next')) $('#feature-page-next').addEventListener('click', function () { state.featurePage += 1; renderFeatureSelection(); });
}
function renderConversation() {
  const container = $('#conversation-messages');
  if (!container) return;
  if (!state.messages.length) {
    container.innerHTML = '<div class="timeline-placeholder">你可以追问当前证据、模型比较或下一步；需要改变方案时，系统会引导到确认卡。</div>';
    return;
  }
  container.innerHTML = state.messages.map(function (message) {
    const roleClass = message.role === 'user' ? 'user-message' : 'assistant-message';
    const label = message.role === 'user' ? '你' : (message.agent || 'Agent');
    const structured = message.structured || {};
    const actions = (structured.next_actions || []).map(function (item) { return '<span class="chat-action">' + escapeHtml(item) + '</span>'; }).join('');
    const feedback = message.role === 'assistant' ? '<div class="chat-feedback"><button type="button" data-chat-feedback="like" data-message-id="' + escapeHtml(message.id) + '">👍</button><button type="button" data-chat-feedback="dislike" data-message-id="' + escapeHtml(message.id) + '">👎</button></div>' : '';
    return '<div class="chat-message ' + roleClass + '"><div class="chat-label">' + escapeHtml(label) + '</div><div class="chat-content">' + escapeHtml(message.content) + '</div>' + (actions ? '<div class="chat-actions">' + actions + '</div>' : '') + feedback + '<small>' + escapeHtml((message.created_at || '').slice(11, 19)) + '</small></div>';
  }).join('');
  container.scrollTop = container.scrollHeight;
  container.querySelectorAll('[data-chat-feedback]').forEach(function (button) { button.addEventListener('click', function () { sendMessageFeedback(button.dataset.messageId, button.dataset.chatFeedback); }); });
}
async function runAnalysis() {
  const dataset = activeDataset();
  if (!state.project || !dataset) return;
  const selects = Array.from(document.querySelectorAll('.analysis-select')).map(function (select) { return select.value; }).filter(Boolean);
  if (!selects.length) return;
  const profile = activeProfile();
  const details = Object.fromEntries((profile.columns_detail || []).map(function (item) { return [item.name, item]; }));
  const target = state.run && state.run.dataset_id === dataset.id && state.run.state && state.run.state.plan && state.run.state.plan.target;
  const targetColumn = target || (profile.target_candidates || [])[0] || null;
  try {
    const result = await api('/api/projects/' + state.project.id + '/analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset_id: dataset.id, spec: { dimensions: selects.map(function (column) { return { column: column, transform: details[column] && details[column].type === 'numeric' ? 'quantile_bins' : 'category', bins: 10 }; }), target: { column: targetColumn }, min_group_size: 50, max_groups: 1000 } }) });
    const rows = (result.analysis && result.analysis.rows) || [];
    $('#analysis-result').innerHTML = rows.length ? '<div class="analysis-meta">返回 ' + rows.length + ' 个组合，隐藏 ' + (result.analysis.suppressed_groups || 0) + ' 个小样本组合</div><div class="analysis-table-wrap"><table><thead><tr>' + selects.map(function (column) { return '<th>' + escapeHtml(column) + '</th>'; }).join('') + '<th>样本</th><th>坏样本率</th></tr></thead><tbody>' + rows.slice(0, 30).map(function (row) { return '<tr>' + selects.map(function (column) { return '<td>' + escapeHtml(row['__dim_' + column] || row[column] || '') + '</td>'; }).join('') + '<td>' + escapeHtml(row.row_count) + '</td><td>' + escapeHtml(row.bad_rate == null ? '—' : (row.bad_rate * 100).toFixed(2) + '%') + '</td></tr>'; }).join('') + '</tbody></table></div>' : '<span class="muted">没有达到最小样本量的组合。</span>';
  } catch (error) { showNotice(error.message, 'block'); }
}
function renderTimeline() {
  const timeline = $('#timeline'); $('#timeline-count').textContent = state.events.length + ' 条事件';
  if (!state.events.length) { timeline.className = 'timeline empty-timeline'; timeline.innerHTML = '<div class="timeline-placeholder">运行开始后，这里会显示节点、工具、审核和产物事件。</div>'; return; }
  timeline.className = 'timeline';
  timeline.innerHTML = state.events.map(function (event) {
    const payload = event.payload || {}; const details = payload.findings && payload.findings.length ? '发现 ' + payload.findings.length + ' 个结构化问题' : payload.tool ? '工具 · ' + payload.tool : payload.actor ? '角色 · ' + payload.actor : '';
    return '<div class="timeline-event"><div class="timeline-dot"></div><div class="timeline-copy"><strong>' + escapeHtml(payload.message || event.event_type) + '</strong><small>' + escapeHtml(phaseLabel(payload.node)) + ' · ' + escapeHtml(details) + ' · ' + escapeHtml((event.created_at || '').slice(11, 19)) + '</small>' + (payload.error ? '<p class="error-text">' + escapeHtml(payload.error) + '</p>' : '') + '</div></div>';
  }).join('');
  timeline.scrollTop = timeline.scrollHeight;
}
function renderActionCard() {
  const card = $('#action-card'); const run = state.run;
  if (!state.datasets.length) { card.innerHTML = '<h3>先导入一个数据集</h3><p>支持 CSV / XLSX。文件留在本机，上传后会先做预检。</p><button class="secondary-button action-button" id="panel-choose">选择文件</button>'; $('#panel-choose').addEventListener('click', function () { $('#file-input').click(); }); return; }
  if (!run) { card.innerHTML = '<h3>准备好了</h3><p>点击右上角“开始分析”，本地 Worker 会先建立画像。</p>'; return; }
  if (run.status === 'awaiting_confirmation') {
    const runState = run.state || {}; const plan = runState.plan || {}; const target = runState.target || {}; const review = runState.plan_review || {}; const cleaning = runState.cleaning || {};
    const split = plan.split || {}; const models = (plan.models || []).map(escapeHtml).join('、') || '待定';
    const targetCandidates = (runState.profile && runState.profile.target_candidates) || [];
    const profileColumns = (runState.profile && runState.profile.columns_detail) || [];
    if (state.confirmationRunId !== run.id) {
      state.confirmationRunId = run.id;
      state.confirmationFeatures = profileColumns.filter(function (item) { return ['numeric', 'categorical'].includes(item.type) && item.name !== (plan.target || target.target); });
      state.featurePage = 1;
      state.confirmationExcluded = new Set((plan.screening && plan.screening.excluded_columns) || []);
    }
    const baselineCandidates = profileColumns.filter(function (item) { return item.type === 'numeric' && item.name !== (plan.target || target.target); });
    const baselineOptions = ['<option value="">不导入既有模型基线</option>'].concat(baselineCandidates.map(function (item) { return '<option value="' + escapeHtml(item.name) + '" ' + (item.name === plan.baseline_column ? 'selected' : '') + '>' + escapeHtml(item.name) + '</option>'; })).join('');
    const targetOptions = targetCandidates.map(function (candidate) { return '<option value="' + escapeHtml(candidate) + '" ' + (candidate === (plan.target || target.target) ? 'selected' : '') + '>' + escapeHtml(candidate) + '</option>'; }).join('');
    const splitOptions = ['time_holdout', 'stratified_holdout'].map(function (method) { return '<option value="' + method + '" ' + (method === (split.method || 'stratified_holdout') ? 'selected' : '') + '>' + (method === 'time_holdout' ? '时间留出' : '分层留出') + '</option>'; }).join('');
    const modelOptions = ['woe_logistic_scorecard', 'logistic_regression', 'random_forest', 'hist_gradient_boosting', 'xgboost'].map(function (model) { return '<label class="model-option"><input type="checkbox" data-confirm-model="' + model + '" ' + ((plan.models || []).includes(model) ? 'checked' : '') + ' />' + model + '</label>'; }).join('');
    const findings = (review.findings || []).map(function (item) { return '<li>' + escapeHtml(item.message || item.code || '需关注') + '</li>'; }).join('');
    const cleaningFindings = (cleaning.requires_confirmation || []).map(function (item) { return '<li>' + escapeHtml(item.message || item.code || '需关注') + '</li>'; }).join('');
    const cleaningButton = run.phase === 'cleaning' && (cleaning.requires_confirmation || []).length && !cleaning.execution ? '<div class="cleaning-actions"><button class="secondary-button action-button" id="apply-cleaning">批准并生成新数据版本</button><button class="text-link action-button" id="skip-cleaning">跳过业务性清洗</button><small class="muted">必须明确选择其一，不能静默跳过。</small></div>' : '';
    const confirmationDisabled = run.phase === 'cleaning' && (cleaning.requires_confirmation || []).length && !cleaning.execution ? ' disabled' : '';
    card.innerHTML = '<h3>需要你的确认</h3><p>Reviewer 已完成计划审核。确认的是业务方案，不需要阅读生成代码。</p>' +
      '<div class="decision-summary"><div><span>Y 字段</span><strong>' + escapeHtml(plan.target || target.target || '待确定') + '</strong></div>' +
      '<div><span>正类比例</span><strong>' + (target.positive_rate == null ? '—' : escapeHtml((target.positive_rate * 100).toFixed(2) + '%')) + '</strong></div>' +
      '<div><span>样本切分</span><strong>' + escapeHtml(split.method || '待定') + ' · ' + escapeHtml(String(split.train || '—')) + '/' + escapeHtml(String(split.validation || '—')) + '/' + escapeHtml(String(split.oot || '—')) + '</strong></div>' +
      '<div><span>候选模型</span><strong>' + models + '</strong></div></div>' +
      (findings ? '<div class="review-findings"><span>Reviewer 提示</span><ul>' + findings + '</ul></div>' : '') +
      (cleaningFindings ? '<div class="review-findings"><span>清洗提示</span><ul>' + cleaningFindings + '</ul></div>' : '') +
      '<div class="decision-controls"><label>确认 Y 字段<select id="confirm-target">' + targetOptions + '</select></label>' +
      '<label>确认样本切分<select id="confirm-split">' + splitOptions + '</select></label>' +
      '<label>既有模型基线（可选）<select id="confirm-baseline">' + baselineOptions + '</select></label>' +
      '<div class="feature-selection"><div class="feature-selection-heading"><span class="control-label">手动排除字段（可选）</span><small id="feature-count">可选 ' + state.confirmationFeatures.length + ' 个</small></div><input id="feature-search" class="feature-search" type="search" placeholder="搜索字段名或中文释义" autocomplete="off" /><div id="feature-list" class="feature-list"></div><small class="muted">规则筛选仍会在训练分区执行；这里的勾选会作为明确业务决定记录。</small></div>' +
      '<div><span class="control-label">本次候选模型</span><div class="model-options">' + modelOptions + '</div></div></div>' +
      cleaningButton + '<button class="primary-button action-button" id="confirm-plan"' + confirmationDisabled + '>确认并继续</button>';
    $('#confirm-plan').addEventListener('click', confirmPlan);
    renderFeatureSelection();
    $('#feature-search').addEventListener('input', function () { state.featurePage = 1; renderFeatureSelection(); });
    $('#confirm-target').addEventListener('change', function () {
      state.confirmationFeatures = profileColumns.filter(function (item) { return ['numeric', 'categorical'].includes(item.type) && item.name !== $('#confirm-target').value; });
      state.featurePage = 1; renderFeatureSelection();
    });
    if ($('#apply-cleaning')) $('#apply-cleaning').addEventListener('click', applyCleaning);
    if ($('#skip-cleaning')) $('#skip-cleaning').addEventListener('click', skipCleaning);
  }
  else if (run.status === 'succeeded') {
    const baseline = (run.state && run.state.report && run.state.report.baseline) || {};
    const reevalButton = baseline.score_column ? '<button class="secondary-button action-button" id="reevaluate-baseline">新 OOT 复评既有模型</button>' : '';
    card.innerHTML = '<h3>本次 Run 已完成</h3><p>模型、代码交付物和报告已保存在本机。你可以打开 HTML 报告，或从当前方案派生隔离的 what-if 实验。</p><a class="secondary-button action-button" href="/api/runs/' + run.id + '/report.html" target="_blank">打开报告</a><div class="action-links"><a class="text-link" href="/api/runs/' + run.id + '/report.xlsx" target="_blank">下载 XLSX</a><a class="text-link" href="/api/runs/' + run.id + '/artifacts.zip" target="_blank">下载完整交付物</a><a class="text-link" href="/api/runs/' + run.id + '/trace.zip" target="_blank">下载 Trace</a></div><button class="secondary-button action-button" id="what-if-run">派生 what-if 实验</button>' + reevalButton;
    $('#what-if-run').addEventListener('click', createWhatIf);
    if ($('#reevaluate-baseline')) $('#reevaluate-baseline').addEventListener('click', reevaluateBaseline);
  }
  else if (run.status === 'paused') { card.innerHTML = '<h3>Run 已暂停</h3><p>已保存最近安全节点，不会把半成品当作成功结果。</p><button class="primary-button action-button" id="resume-run">恢复运行</button><button class="secondary-button action-button" id="cancel-run">取消 Run</button>'; $('#resume-run').addEventListener('click', resumeRun); $('#cancel-run').addEventListener('click', cancelRun); }
  else if (run.status === 'queued' || run.status === 'running') { card.innerHTML = '<h3>正在运行</h3><p>页面会持续接收节点和工具事件。你可以暂停到最近安全节点，或取消本次 Run。</p><button class="secondary-button action-button" id="pause-run">暂停</button><button class="secondary-button action-button" id="cancel-run">取消 Run</button>'; $('#pause-run').addEventListener('click', pauseRun); $('#cancel-run').addEventListener('click', cancelRun); }
  else if (run.status === 'failed' || run.status === 'blocked') card.innerHTML = '<h3>需要处理</h3><p>' + escapeHtml(run.error || '查看时间线中的结构化问题。') + '</p>';
  else if (run.status === 'cancelled') card.innerHTML = '<h3>Run 已取消</h3><p>未完成产物已与正式结果隔离。可以修改方案后新建一次 Run。</p>';
  else card.innerHTML = '<h3>正在运行</h3><p>页面会持续接收节点和工具事件。可以离开后回来继续查看。</p>';
}
function chartNumber(value) { const number = Number(value); return Number.isFinite(number) ? number : null; }
function chartEmpty(message) { return '<div class="chart-empty">' + escapeHtml(message || '暂无可视化证据') + '</div>'; }
function chartCanvas(label, yMax, yFormatter) {
  const width = 460; const height = 240; const left = 46; const right = 16; const top = 14; const bottom = 34; const plotWidth = width - left - right; const plotHeight = height - top - bottom; const max = yMax > 0 ? yMax : 1;
  const x = function (value) { return left + Math.max(0, Math.min(1, value)) * plotWidth; }; const y = function (value) { return top + plotHeight - Math.max(0, Math.min(max, value)) / max * plotHeight; };
  let svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="' + escapeHtml(label) + '"><rect class="chart-background" x="0" y="0" width="' + width + '" height="' + height + '" rx="12" />';
  [0, 0.25, 0.5, 0.75, 1].forEach(function (ratio) { const value = max * ratio; const yPosition = y(value); svg += '<line class="chart-grid" x1="' + left + '" y1="' + yPosition.toFixed(2) + '" x2="' + (left + plotWidth) + '" y2="' + yPosition.toFixed(2) + '" /><text class="chart-axis-label" x="' + (left - 8) + '" y="' + (yPosition + 3).toFixed(2) + '" text-anchor="end">' + escapeHtml(yFormatter ? yFormatter(value) : value.toFixed(2)) + '</text>'; });
  svg += '<line class="chart-axis" x1="' + left + '" y1="' + (top + plotHeight) + '" x2="' + (left + plotWidth) + '" y2="' + (top + plotHeight) + '" /><line class="chart-axis" x1="' + left + '" y1="' + top + '" x2="' + left + '" y2="' + (top + plotHeight) + '" />';
  return { svg: svg, x: x, y: y, left: left, top: top, plotWidth: plotWidth, plotHeight: plotHeight, bottom: bottom, width: width, height: height };
}
function chartLine(points, color, shell, xKey, yKey) {
  const coordinates = points.map(function (item, index) { const xValue = xKey ? xKey(item, index, points.length) : index / Math.max(points.length - 1, 1); const yValue = chartNumber(yKey(item)); return yValue == null ? null : shell.x(xValue).toFixed(2) + ',' + shell.y(yValue).toFixed(2); }).filter(Boolean);
  return coordinates.length > 1 ? '<polyline class="chart-line" stroke="' + color + '" points="' + coordinates.join(' ') + '" />' : '';
}
function chartLegend(items) { return '<div class="chart-legend">' + items.map(function (item) { return '<span><i style="background:' + escapeHtml(item.color) + '"></i>' + escapeHtml(item.label) + '</span>'; }).join('') + '</div>'; }
function renderRocChart(metrics) {
  const target = $('#chart-roc'); if (!target) return;
  const points = (metrics && metrics.roc_curve || []).filter(function (item) { return chartNumber(item.fpr) != null && chartNumber(item.tpr) != null; });
  if (points.length < 2) { target.innerHTML = chartEmpty('该 Run 未保存 ROC 曲线点；重新运行后会生成。'); return; }
  const shell = chartCanvas('验证集 ROC 曲线', 1, function (value) { return value.toFixed(1); }); let svg = shell.svg + '<line class="chart-reference" x1="' + shell.x(0) + '" y1="' + shell.y(0) + '" x2="' + shell.x(1) + '" y2="' + shell.y(1) + '" />' + chartLine(points, '#1d8067', shell, function (item) { return chartNumber(item.fpr); }, function (item) { return item.tpr; }); svg += '<text class="chart-axis-caption" x="' + (shell.left + shell.plotWidth / 2) + '" y="' + (shell.height - 7) + '" text-anchor="middle">False Positive Rate</text><text class="chart-axis-caption" transform="translate(12 ' + (shell.top + shell.plotHeight / 2) + ') rotate(-90)" text-anchor="middle">True Positive Rate</text></svg>'; target.innerHTML = svg + chartLegend([{ color: '#1d8067', label: 'ROC' }, { color: '#b7c7c1', label: '随机基线' }]);
}
function renderKsChart(metrics) {
  const target = $('#chart-ks'); if (!target) return;
  const points = (metrics && metrics.ks_curve || []).filter(function (item) { return chartNumber(item.fpr) != null && chartNumber(item.tpr) != null; });
  if (points.length < 2) { target.innerHTML = chartEmpty('该 Run 未保存 KS 曲线点；重新运行后会生成。'); return; }
  const shell = chartCanvas('验证集 KS 曲线', 1, function (value) { return value.toFixed(1); }); let svg = shell.svg + chartLine(points, '#1d8067', shell, null, function (item) { return item.tpr; }) + chartLine(points, '#d88b45', shell, null, function (item) { return item.fpr; }) + chartLine(points, '#8d5aa7', shell, null, function (item) { return item.ks; }); svg += '<text class="chart-axis-caption" x="' + (shell.left + shell.plotWidth / 2) + '" y="' + (shell.height - 7) + '" text-anchor="middle">排序分位（由高分到低分）</text><text class="chart-axis-caption" transform="translate(12 ' + (shell.top + shell.plotHeight / 2) + ') rotate(-90)" text-anchor="middle">Rate</text></svg>'; target.innerHTML = svg + chartLegend([{ color: '#1d8067', label: 'TPR' }, { color: '#d88b45', label: 'FPR' }, { color: '#8d5aa7', label: 'KS' }]);
}
function renderCalibrationChart(metrics) {
  const target = $('#chart-calibration'); if (!target) return;
  const points = (metrics && metrics.calibration || []).filter(function (item) { return chartNumber(item.predicted_rate) != null && chartNumber(item.observed_rate) != null; });
  if (!points.length) { target.innerHTML = chartEmpty('暂无校准分箱结果。'); return; }
  const shell = chartCanvas('验证集校准曲线', 1, function (value) { return value.toFixed(1); }); let svg = shell.svg + '<line class="chart-reference" x1="' + shell.x(0) + '" y1="' + shell.y(0) + '" x2="' + shell.x(1) + '" y2="' + shell.y(1) + '" />' + chartLine(points, '#1d8067', shell, function (item) { return chartNumber(item.predicted_rate); }, function (item) { return item.observed_rate; }); points.forEach(function (item) { svg += '<circle class="chart-point" cx="' + shell.x(chartNumber(item.predicted_rate)) + '" cy="' + shell.y(chartNumber(item.observed_rate)) + '" r="3" />'; }); svg += '<text class="chart-axis-caption" x="' + (shell.left + shell.plotWidth / 2) + '" y="' + (shell.height - 7) + '" text-anchor="middle">预测坏率</text><text class="chart-axis-caption" transform="translate(12 ' + (shell.top + shell.plotHeight / 2) + ') rotate(-90)" text-anchor="middle">实际坏率</text></svg>'; target.innerHTML = svg + chartLegend([{ color: '#1d8067', label: '实际 / 预测' }, { color: '#b7c7c1', label: '理想校准' }]);
}
function renderImportanceChart(champion, scorecard) {
  const target = $('#chart-importance'); if (!target) return;
  const rows = (champion && champion.feature_importance || []).map(function (item) { return { label: item.feature || item.column, value: chartNumber(item.importance != null ? item.importance : (item.absolute_coefficient != null ? item.absolute_coefficient : item.iv)) }; }).filter(function (item) { return item.label && item.value != null; }).sort(function (a, b) { return b.value - a.value; }).slice(0, 8);
  if (!rows.length && scorecard) return renderImportanceChart({ feature_importance: scorecard.feature_importance || [] }, null);
  if (!rows.length) { target.innerHTML = chartEmpty('暂无变量重要性结果。'); return; }
  const max = Math.max.apply(null, rows.map(function (item) { return item.value; })) || 1; const rowHeight = 23; const width = 460; const height = Math.max(150, rows.length * rowHeight + 42); let svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="冠军模型变量重要性"><rect class="chart-background" x="0" y="0" width="' + width + '" height="' + height + '" rx="12" />'; rows.forEach(function (item, index) { const y = 20 + index * rowHeight; const bar = Math.max(2, item.value / max * 250); svg += '<text class="chart-bar-label" x="14" y="' + (y + 13) + '">' + escapeHtml(String(item.label).slice(0, 24)) + '</text><rect class="chart-bar" x="170" y="' + y + '" width="' + bar.toFixed(2) + '" height="14" rx="7" /><text class="chart-bar-value" x="' + (180 + bar) + '" y="' + (y + 12) + '">' + escapeHtml(item.value.toFixed(4)) + '</text>'; }); svg += '</svg>'; target.innerHTML = svg;
}
function renderPsiChart(stability) {
  const target = $('#chart-psi'); if (!target) return;
  const rows = (stability || []).map(function (item) { return { label: item.feature, validation: chartNumber((item.validation || {}).psi), oot: chartNumber((item.oot || {}).psi) }; }).filter(function (item) { return item.label && (item.validation != null || item.oot != null); }).sort(function (a, b) { return Math.max(b.validation || 0, b.oot || 0) - Math.max(a.validation || 0, a.oot || 0); }).slice(0, 8);
  if (!rows.length) { target.innerHTML = chartEmpty('暂无验证 / OOT PSI 结果。'); return; }
  const max = Math.max(0.25, Math.max.apply(null, rows.map(function (item) { return Math.max(item.validation || 0, item.oot || 0); }))); const width = 460; const rowHeight = 25; const height = Math.max(160, rows.length * rowHeight + 42); const left = 142; const plotWidth = 280; let svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="变量 PSI 稳定性"><rect class="chart-background" x="0" y="0" width="' + width + '" height="' + height + '" rx="12" /><line class="chart-reference" x1="' + (left + 0.25 / max * plotWidth) + '" y1="14" x2="' + (left + 0.25 / max * plotWidth) + '" y2="' + (height - 22) + '" />'; rows.forEach(function (item, index) { const y = 18 + index * rowHeight; const validation = (item.validation || 0) / max * plotWidth; const oot = (item.oot || 0) / max * plotWidth; svg += '<text class="chart-bar-label" x="12" y="' + (y + 13) + '">' + escapeHtml(String(item.label).slice(0, 20)) + '</text><rect class="chart-bar chart-bar-validation" x="' + left + '" y="' + y + '" width="' + Math.max(1, validation).toFixed(2) + '" height="7" rx="3.5" /><rect class="chart-bar chart-bar-oot" x="' + left + '" y="' + (y + 9) + '" width="' + Math.max(1, oot).toFixed(2) + '" height="7" rx="3.5" />'; }); svg += '<text class="chart-axis-caption" x="' + (left + plotWidth / 2) + '" y="' + (height - 7) + '" text-anchor="middle">PSI（虚线 = 0.25）</text></svg>'; target.innerHTML = svg + chartLegend([{ color: '#1d8067', label: '验证' }, { color: '#d88b45', label: 'OOT' }]);
}
function renderWoeChart(scorecard) {
  const target = $('#chart-woe'); if (!target) return;
  const bins = scorecard && scorecard.bins || {}; const importance = scorecard && scorecard.feature_importance || []; const feature = (importance[0] && importance[0].feature) || Object.keys(bins)[0]; const rows = feature && bins[feature] && bins[feature].rows || [];
  if (!rows.length) { target.innerHTML = chartEmpty('当前 Run 没有评分卡 WOE 分箱结果。'); return; }
  const values = rows.map(function (item) { return chartNumber(item.woe); }).filter(function (value) { return value != null; }); const bound = Math.max(0.2, Math.max.apply(null, values.map(Math.abs))); const width = 460; const height = 220; const left = 48; const right = 16; const top = 18; const bottom = 40; const plotWidth = width - left - right; const plotHeight = height - top - bottom; const x = function (index) { return left + (index + 0.5) / rows.length * plotWidth; }; const y = function (value) { return top + plotHeight / 2 - value / bound * (plotHeight / 2 - 5); }; let svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="' + escapeHtml(String(feature) + ' WOE 分箱') + '"><rect class="chart-background" x="0" y="0" width="' + width + '" height="' + height + '" rx="12" /><line class="chart-axis" x1="' + left + '" y1="' + (top + plotHeight / 2) + '" x2="' + (left + plotWidth) + '" y2="' + (top + plotHeight / 2) + '" />'; rows.forEach(function (item, index) { const value = chartNumber(item.woe); if (value == null) return; const barTop = Math.min(y(value), top + plotHeight / 2); const barHeight = Math.max(2, Math.abs(y(value) - (top + plotHeight / 2))); svg += '<rect class="chart-bar ' + (value >= 0 ? 'chart-bar-positive' : 'chart-bar-negative') + '" x="' + (x(index) - Math.max(7, plotWidth / rows.length * 0.28)) + '" y="' + barTop + '" width="' + Math.max(14, plotWidth / rows.length * 0.56) + '" height="' + barHeight + '" rx="4" /><text class="chart-axis-label" x="' + x(index) + '" y="' + (height - 19) + '" text-anchor="middle">' + escapeHtml(String(item.bin || '').slice(0, 12)) + '</text>'; }); svg += '<text class="chart-axis-caption" x="' + (left + plotWidth / 2) + '" y="' + (height - 5) + '" text-anchor="middle">' + escapeHtml(String(feature).slice(0, 32)) + '</text><text class="chart-axis-caption" transform="translate(12 ' + (top + plotHeight / 2) + ') rotate(-90)" text-anchor="middle">WOE</text></svg>'; target.innerHTML = svg + chartLegend([{ color: '#1d8067', label: '正 WOE' }, { color: '#d88b45', label: '负 WOE' }]);
}
function renderReportCharts(report) {
  const champion = report && report.champion || {}; const metrics = champion.validation || {};
  renderRocChart(metrics); renderKsChart(metrics); renderCalibrationChart(metrics); renderImportanceChart(champion, report && report.scorecard); renderPsiChart(report && report.stability && report.stability.features); renderWoeChart(report && report.scorecard);
}
function renderReport(report) {
  if (!report) return; $('#report-panel').classList.remove('hidden'); $('#report-empty-state').classList.add('hidden'); $('#report-tab').classList.add('has-result'); $('#report-link').href = '/api/runs/' + state.run.id + '/report.html';
  const champion = report.champion || {}; const metrics = champion.validation || {};
  const calibration = metrics.calibration || []; const stability = report.stability && report.stability.features || [];
  const values = [['冠军建议', champion.name || '—'], ['验证 ROC-AUC', metrics.roc_auc == null ? '—' : metrics.roc_auc], ['验证 KS', metrics.ks == null ? '—' : metrics.ks], ['最终变量', report.selection && report.selection.funnel ? report.selection.funnel.final : '—']];
  $('#metric-grid').innerHTML = values.map(function (item) { return '<div class="metric-card"><span>' + item[0] + '</span><strong>' + escapeHtml(item[1]) + '</strong></div>'; }).join('');
  $('#report-narrative').textContent = report.narrative || '报告已生成。';
  const decisions = report.selection && report.selection.decisions || [];
  const excluded = decisions.filter(function (item) { return item.status !== 'included'; });
  const calibrationGap = calibration.length ? Math.max.apply(null, calibration.map(function (item) { return Number(item.absolute_gap || 0); })).toFixed(4) : '—';
  const highPsi = stability.filter(function (item) { return ['review', 'high'].includes(item.validation && item.validation.review_flag) || ['review', 'high'].includes(item.oot && item.oot.review_flag); }).length;
  const reevaluations = report.baseline_reevaluations || [];
  const reevaluationNote = reevaluations.length ? '<strong>既有模型新 OOT 复评</strong><ul>' + reevaluations.map(function (item) { const metrics = item.metrics || {}; return '<li>' + escapeHtml(item.dataset_filename || item.dataset_id || '新数据集') + ' · ROC-AUC ' + escapeHtml(metrics.roc_auc == null ? '—' : metrics.roc_auc) + ' · KS ' + escapeHtml(metrics.ks == null ? '—' : metrics.ks) + ' · 固定通过率坏样本捕获 ' + escapeHtml((item.fixed_rate || {}).bad_capture_rate == null ? '—' : (item.fixed_rate || {}).bad_capture_rate) + '</li>'; }).join('') + '</ul>' : '';
  const review = report.code_review || {};
  const imbalance = report.imbalance_policy || {};
  const reviewLabel = review.verdict === 'pass' ? '通过' : review.verdict === 'block' ? '阻断' : (review.verdict || '—');
  $('#report-details').innerHTML = '<div class="report-facts"><span>校准最大绝对差</span><strong>' + calibrationGap + '</strong><span>需稳定性复核变量</span><strong>' + highPsi + '</strong><span>代码 Reviewer</span><strong>' + escapeHtml(reviewLabel) + '</strong><span>训练正/负类</span><strong>' + escapeHtml(String(imbalance.train_positive_count == null ? '—' : imbalance.train_positive_count) + ' / ' + String(imbalance.train_negative_count == null ? '—' : imbalance.train_negative_count)) + '</strong></div>' + (excluded.length ? '<strong>字段处理摘要</strong><ul>' + excluded.map(function (item) { return '<li><code>' + escapeHtml(item.column) + '</code> · ' + escapeHtml(item.status) + ' · ' + escapeHtml((item.reasons || []).join('、') || '规则排除') + '</li>'; }).join('') + '</ul>' : '<strong>字段处理摘要</strong><span>没有字段被规则排除。</span>') + reevaluationNote;
  renderReportCharts(report);
  renderSelectionTable(report);
  renderNarrativeEditor(report);
}
function renderSelectionTable(report) {
  const panel = $('#selection-table-panel'); if (!panel) return;
  const rows = (report.selection && report.selection.decisions) || [];
  if (!rows.length) { panel.innerHTML = ''; return; }
  const planExcluded = new Set(((state.run && state.run.state && state.run.state.plan && state.run.state.plan.screening) || {}).excluded_columns || []);
  if (!state.selectionExcluded.size) state.selectionExcluded = new Set(planExcluded);
  panel.innerHTML = '<div class="selection-table-heading"><div><strong>变量筛选明细</strong><small>训练分区拟合的 IV/缺失/规则结果；勾选后可派生隔离 what-if。</small></div><span id="selection-count" class="chip neutral">' + rows.length + ' 个字段</span></div><div class="selection-table-controls"><input id="selection-search" class="feature-search" type="search" placeholder="搜索字段名" autocomplete="off" /><select id="selection-status"><option value="all">全部状态</option><option value="included">入选</option><option value="excluded">排除</option><option value="blocked">阻断</option></select><select id="selection-sort"><option value="iv">按 IV 降序</option><option value="missing">按缺失率降序</option><option value="column">按字段名</option></select></div><div id="selection-table-list" class="selection-table-list"></div><div id="selection-pagination"></div><div class="selection-table-actions"><small class="muted">变量按页加载，避免大字段表阻塞浏览器；筛选和排序会作用于全部结果。</small><button type="button" id="selection-what-if" class="secondary-button small">用勾选字段派生 what-if</button></div>';
  function redraw() {
    const query = ($('#selection-search').value || '').trim().toLowerCase();
    const status = $('#selection-status').value;
    const sort = $('#selection-sort').value;
    const filtered = rows.filter(function (item) { return (!query || String(item.column || '').toLowerCase().includes(query)) && (status === 'all' || item.status === status); }).sort(function (a, b) {
      if (sort === 'column') return String(a.column || '').localeCompare(String(b.column || ''));
      const key = sort === 'missing' ? 'missing_rate' : 'iv';
      return Number(b[key] == null ? -1 : b[key]) - Number(a[key] == null ? -1 : a[key]);
    });
    const pageSize = 50; const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize)); state.selectionPage = Math.min(Math.max(state.selectionPage || 1, 1), pageCount); const start = (state.selectionPage - 1) * pageSize; const visible = filtered.slice(start, start + pageSize);
    $('#selection-count').textContent = (query || status !== 'all' ? '匹配 ' + filtered.length : rows.length) + ' 个字段' + (pageCount > 1 ? ' · 第 ' + state.selectionPage + ' / ' + pageCount + ' 页' : '');
    $('#selection-table-list').innerHTML = visible.length ? '<table class="selection-table"><thead><tr><th>what-if</th><th>字段</th><th>状态</th><th>缺失率</th><th>IV</th><th>原因</th></tr></thead><tbody>' + visible.map(function (item) { return '<tr><td><input type="checkbox" data-selection-column="' + escapeHtml(item.column) + '" ' + (state.selectionExcluded.has(item.column) ? 'checked' : '') + ' ' + (item.status === 'blocked' ? 'disabled' : '') + ' /></td><td><code>' + escapeHtml(item.column) + '</code></td><td><span class="selection-status ' + escapeHtml(item.status) + '">' + escapeHtml(item.status) + '</span></td><td>' + (item.missing_rate == null ? '—' : escapeHtml((Number(item.missing_rate) * 100).toFixed(1) + '%')) + '</td><td>' + (item.iv == null ? '—' : escapeHtml(Number(item.iv).toFixed(4))) + '</td><td>' + escapeHtml((item.reasons || []).join('、') || '—') + '</td></tr>'; }).join('') + '</tbody></table>' : '<span class="muted">没有匹配变量。</span>';
    $('#selection-table-list').querySelectorAll('[data-selection-column]').forEach(function (input) { input.addEventListener('change', function () { if (input.checked) state.selectionExcluded.add(input.dataset.selectionColumn); else state.selectionExcluded.delete(input.dataset.selectionColumn); }); });
    const pagination = $('#selection-pagination'); if (pagination) { pagination.innerHTML = pageCount > 1 ? '<div class="table-pagination"><button type="button" class="secondary-button small" id="selection-page-prev"' + (state.selectionPage === 1 ? ' disabled' : '') + '>上一页</button><span>第 ' + state.selectionPage + ' / ' + pageCount + ' 页</span><button type="button" class="secondary-button small" id="selection-page-next"' + (state.selectionPage === pageCount ? ' disabled' : '') + '>下一页</button></div>' : ''; if ($('#selection-page-prev')) $('#selection-page-prev').addEventListener('click', function () { state.selectionPage -= 1; redraw(); }); if ($('#selection-page-next')) $('#selection-page-next').addEventListener('click', function () { state.selectionPage += 1; redraw(); }); }
  }
  $('#selection-search').addEventListener('input', function () { state.selectionPage = 1; redraw(); }); $('#selection-status').addEventListener('change', function () { state.selectionPage = 1; redraw(); }); $('#selection-sort').addEventListener('change', function () { state.selectionPage = 1; redraw(); }); redraw();
  $('#selection-what-if').addEventListener('click', function () { createWhatIf({ excluded_features: Array.from(state.selectionExcluded) }); });
}
function renderNarrativeEditor(report) {
  const panel = $('#report-narrative-editor'); if (!panel) return;
  const narrative = report.narrative_sections || {}; const sections = narrative.sections || [];
  if (!sections.length) { panel.innerHTML = ''; return; }
  if (narrative.locked) {
    panel.innerHTML = '<div class="narrative-lock"><strong>报告叙事已锁定</strong><span>revision ' + escapeHtml(narrative.revision || 0) + ' · 文本已纳入 checksums</span></div>';
    return;
  }
  panel.innerHTML = '<div class="narrative-editor-heading"><div><strong>报告叙事编辑</strong><small>只修改文字；指标、样本、模型和评分卡仍来自确定性产物。</small></div><span class="chip neutral">未锁定</span></div>' + sections.map(function (item) { return '<label class="narrative-edit-row"><span>' + escapeHtml(item.title || item.id) + '</span><textarea data-narrative-id="' + escapeHtml(item.id) + '" maxlength="3000">' + escapeHtml(item.text || '') + '</textarea><small>证据：' + escapeHtml((item.evidence_refs || []).join('、')) + '</small></label>'; }).join('') + '<div class="narrative-editor-actions"><button type="button" class="secondary-button small" id="save-narrative-draft">保存叙事草稿</button><button type="button" class="primary-button small" id="lock-narrative">保存并锁定</button></div>';
  $('#save-narrative-draft').addEventListener('click', function () { saveNarrative(false); });
  $('#lock-narrative').addEventListener('click', function () { saveNarrative(true); });
}
async function saveNarrative(lock) {
  const panel = $('#report-narrative-editor'); if (!state.run || !panel) return;
  const sections = Array.from(panel.querySelectorAll('[data-narrative-id]')).map(function (textarea) { return { id: textarea.dataset.narrativeId, text: textarea.value }; });
  try {
    const result = await api('/api/runs/' + state.run.id + '/report/narrative', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sections: sections, lock: lock }) });
    showNotice(lock ? '报告叙事已锁定并写入哈希清单。' : '报告叙事草稿已保存。');
    renderReport(result.report);
  } catch (error) { showNotice(error.message, 'block'); }
}
function connectStream() {
  if (state.eventSource) state.eventSource.close(); if (!state.run) return;
  state.eventSource = new EventSource('/api/runs/' + state.run.id + '/events/stream?after=' + (state.events.length ? state.events[state.events.length - 1].sequence : 0));
  state.eventSource.onmessage = async function (message) {
    const event = JSON.parse(message.data);
    if (!state.events.some(function (item) { return item.sequence === event.sequence; })) state.events.push(event);
    renderTimeline(); renderRun(); await syncRun();
  };
  state.eventSource.onerror = function () { if (state.run && !['succeeded', 'failed', 'blocked', 'cancelled', 'paused'].includes(state.run.status)) setTimeout(connectStream, 1200); };
}
async function syncRun() {
  if (!state.run) return; const data = await api('/api/runs/' + state.run.id); state.run = data.run; renderRun(); renderStages();
  await loadProviderRequests();
  if (state.run.status === 'succeeded') { const report = await api('/api/runs/' + state.run.id + '/report'); renderReport(report); }
}
async function createProject() {
  const values = await openDialog({ title: '新建项目', message: '为这次风控建模任务起一个容易识别的名称。', submitLabel: '创建', fields: [{ name: 'name', label: '项目名称', value: '我的风控建模项目', required: true, maxlength: 120 }] });
  const name = values && String(values.name || '').trim(); if (!name) return;
  setAppView('project');
  const data = await api('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name }) });
  state.project = data.project; await loadProjects(); await selectProject(data.project.id);
}
async function toggleArchiveProject() {
  if (!state.project) return;
  const archived = state.project.status === 'archived';
  if (!archived && !(await openDialog({ title: '归档项目', message: '本地数据和报告会保留，但归档期间不能上传、分析或启动 Run。', submitLabel: '确认归档', confirm: true }))) return;
  try {
    const endpoint = archived ? '/api/projects/' + state.project.id + '/restore' : '/api/projects/' + state.project.id + '/archive';
    const result = await api(endpoint, { method: 'POST' });
    showNotice(result.message || (archived ? '项目已恢复。' : '项目已归档。'));
    await loadProjects(); await selectProject(state.project.id);
  } catch (error) { showNotice(error.message, 'block'); }
}
async function deleteProject() {
  if (!state.project || !(await openDialog({ title: '删除项目', message: '这会移除本机项目目录、数据版本、Run 和报告，且不可恢复。', submitLabel: '确认删除', confirm: true }))) return;
  try {
    await api('/api/projects/' + state.project.id, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: true }) });
    state.project = null; state.run = null; state.datasets = []; state.dictionaries = []; state.runs = [];
    showNotice('项目及其本地文件已删除。'); await loadProjects(); renderProject();
  } catch (error) { showNotice(error.message, 'block'); }
}
async function createDemo() {
  if (!state.project) await createProject(); if (!state.project) return;
  const data = await api('/api/projects/' + state.project.id + '/demo', { method: 'POST' }); showNotice(data.message); await selectProject(state.project.id);
}
async function uploadFile(file) {
  if (!state.project || !file) return; const form = new FormData(); form.append('file', file);
  try {
    const inspectionForm = new FormData(); inspectionForm.append('file', file);
    const inspection = await api('/api/projects/' + state.project.id + '/datasets/inspect', { method: 'POST', body: inspectionForm });
    if (inspection.requires_sheet) { const values = await openDialog({ title: '选择工作表', message: '这个 Excel 文件包含多个 Sheet，请选择要导入的工作表。', submitLabel: '导入', fields: [{ name: 'sheet', label: 'Sheet', type: 'select', options: inspection.sheets, value: inspection.sheets[0], required: true }] }); if (!values || !values.sheet) return; form.append('sheet', values.sheet); }
    const data = await api('/api/projects/' + state.project.id + '/datasets', { method: 'POST', body: form }); showNotice(data.message); await selectProject(state.project.id);
  }
  catch (error) { showNotice(error.message, 'block'); }
}
async function uploadDictionary(file) {
  if (!state.project || !file) return;
  const form = new FormData(); form.append('file', file);
  try {
    const inspectionForm = new FormData(); inspectionForm.append('file', file);
    const inspection = await api('/api/projects/' + state.project.id + '/datasets/inspect', { method: 'POST', body: inspectionForm });
    if (inspection.requires_sheet) { const values = await openDialog({ title: '选择数据字典 Sheet', message: '请选择存放字段中文名、口径和来源信息的工作表。', submitLabel: '绑定', fields: [{ name: 'sheet', label: 'Sheet', type: 'select', options: inspection.sheets, value: inspection.sheets[0], required: true }] }); if (!values || !values.sheet) return; form.append('sheet', values.sheet); }
    const result = await api('/api/projects/' + state.project.id + '/dictionaries', { method: 'POST', body: form });
    showNotice(result.message || '数据字典已保存到本机。');
    await selectProject(state.project.id);
  } catch (error) { showNotice(error.message, 'block'); }
}
async function startRun() {
  const dataset = activeDataset();
  if (!state.project || !dataset) return;
  try {
    const data = await api('/api/projects/' + state.project.id + '/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset_id: dataset.id, mode: state.defaultMode }) });
    state.run = data.run; state.events = []; state.providerRequests = []; state.lastAutoPhase = null; await loadConversation(); renderProject(); await loadProviderRequests(); connectStream();
  } catch (error) { showNotice(error.message, 'block'); }
}
async function confirmPlan() {
  if (!state.run) return;
  const target = $('#confirm-target') && $('#confirm-target').value;
  const splitMethod = $('#confirm-split') && $('#confirm-split').value;
  const baselineColumn = $('#confirm-baseline') && $('#confirm-baseline').value;
  const models = Array.from(document.querySelectorAll('[data-confirm-model]:checked')).map(function (input) { return input.dataset.confirmModel; });
  if (!target || !splitMethod || !models.length) { showNotice('请先确认 Y、样本切分，并至少选择一个候选模型。', 'block'); return; }
  try {
    await api('/api/runs/' + state.run.id + '/decision', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'plan_confirmation', values: { target: target, split_method: splitMethod, models: models, baseline_column: baselineColumn || null, excluded_features: Array.from(state.confirmationExcluded), reviewed_without_code_reading: true } }) });
    showNotice('已记录正式确认，继续本地筛选与训练。'); await syncRun(); connectStream();
  } catch (error) { showNotice(error.message, 'block'); }
}
async function applyCleaning() {
  if (!state.run) return;
  const cleaning = state.run.state && state.run.state.cleaning || {};
  const actions = (cleaning.requires_confirmation || []).map(function (item) { return { code: item.code, columns: item.columns || [] }; });
  if (!actions.length || !(await openDialog({ title: '执行数据清洗', message: '系统会保留原数据，并创建新的本地数据版本。', submitLabel: '确认执行', confirm: true }))) return;
  try { await api('/api/runs/' + state.run.id + '/clean', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ actions: actions }) }); showNotice('清洗已执行并生成新的本地数据版本。'); await syncRun(); } catch (error) { showNotice(error.message, 'block'); }
}
async function skipCleaning() {
  try {
    await api('/api/runs/' + state.run.id + '/clean', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ actions: [] }) });
    showNotice('已记录跳过业务性清洗，原始数据版本保持不变。');
    await syncRun();
  } catch (error) { showNotice(error.message, 'block'); }
}
async function createWhatIf(changes) {
  if (!state.project || !state.run) return;
  if (!changes) {
    const currentPlan = state.run.state && state.run.state.plan || {};
    const currentScreening = currentPlan.screening || {};
    const values = await openDialog({ title: '派生 what-if 实验', message: '只改变这一个筛选阈值，正式 Run 不会被覆盖。', submitLabel: '启动实验', fields: [{ name: 'min_iv', label: '新的最小 IV 阈值', type: 'number', value: String(currentScreening.min_iv == null ? 0.005 : currentScreening.min_iv), min: 0, max: 10, step: 0.001, required: true }] });
    if (!values) return;
    const minIv = Number(values.min_iv);
    if (!Number.isFinite(minIv) || minIv < 0 || minIv > 10) { showNotice('IV 阈值必须是 0—10 的数值。', 'block'); return; }
    changes = { min_iv: minIv };
  }
  try {
    const result = await api('/api/projects/' + state.project.id + '/what-if', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_run_id: state.run.id, changes: changes }) });
    showNotice('what-if 实验已隔离启动，不会覆盖正式 Run。');
    state.run = result.run; state.events = []; state.providerRequests = []; state.selectionExcluded = new Set(); state.lastAutoPhase = null; await loadConversation(); renderProject(); await loadProviderRequests(); connectStream();
  } catch (error) { showNotice(error.message, 'block'); }
}
async function pauseRun() { if (!state.run) return; try { await api('/api/runs/' + state.run.id + '/pause', { method: 'POST' }); await syncRun(); if (state.eventSource) state.eventSource.close(); } catch (error) { showNotice(error.message, 'block'); } }
async function resumeRun() { if (!state.run) return; try { await api('/api/runs/' + state.run.id + '/resume', { method: 'POST' }); await syncRun(); connectStream(); } catch (error) { showNotice(error.message, 'block'); } }
async function cancelRun() { if (!state.run || !(await openDialog({ title: '取消当前 Run', message: '未完成产物不会作为正式结果。', submitLabel: '确认取消', confirm: true }))) return; try { await api('/api/runs/' + state.run.id + '/cancel', { method: 'POST' }); await syncRun(); if (state.eventSource) state.eventSource.close(); } catch (error) { showNotice(error.message, 'block'); } }
async function reevaluateBaseline() {
  if (!state.run) return;
  const baseline = (state.run.state && state.run.state.report && state.run.state.report.baseline) || {};
  if (!baseline.score_column) { showNotice('当前 Run 没有可复评的既有模型基线。', 'block'); return; }
  const datasets = state.datasets || [];
  if (!datasets.length) return;
  const values = await openDialog({ title: '既有模型 OOT 复评', message: '选择一个数据版本，并指定其中的基线分数列。', submitLabel: '开始复评', fields: [{ name: 'dataset_id', label: '复评数据版本', type: 'select', options: datasets.map(function (item) { return { value: item.id, label: item.filename }; }), value: datasets[0].id, required: true }, { name: 'score_column', label: '基线分数列', value: baseline.score_column, required: true }] });
  if (!values || !values.dataset_id || !String(values.score_column || '').trim()) return;
  try {
    const result = await api('/api/runs/' + state.run.id + '/baseline/reevaluate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset_id: values.dataset_id, score_column: String(values.score_column).trim(), approval_rate: 0.8 }) });
    state.run.state = state.run.state || {}; state.run.state.report = result.report;
    showNotice('新 OOT 复评已完成，阈值沿用正式 Run 的验证集冻结值。');
    renderReport(result.report); renderActionCard(); await loadProviderRequests();
  } catch (error) { showNotice(error.message, 'block'); }
}
function selectedProviderPreset() {
  const form = $('#settings-form'); const provider = form && form.elements.provider ? form.elements.provider.value : 'custom';
  return (state.providerPresets || {})[provider] || null;
}
function updateProviderHint() {
  const hint = $('#provider-hint'); const preset = selectedProviderPreset(); const form = $('#settings-form');
  if (!hint || !form) return;
  const format = form.elements.api_format.value; const defaults = preset && preset.defaults && preset.defaults[format];
  if (preset && defaults) hint.textContent = preset.label + ' · ' + (format === 'anthropic' ? 'Anthropic Messages' : 'OpenAI Chat Completions') + ' · 预置地址 ' + (defaults.base_url || '请填写自定义地址');
  else if (preset) hint.textContent = preset.label + ' 不提供当前 API 格式，请切换格式或使用自定义 Provider。';
  else hint.textContent = '可选择预置，也可以使用自定义 Base URL；API Key 只用于本机请求。';
}
function applyProviderPreset() {
  const form = $('#settings-form'); const preset = selectedProviderPreset(); if (!form || !preset) { updateProviderHint(); return; }
  const available = preset.formats || ['openai', 'anthropic'];
  if (!available.includes(form.elements.api_format.value)) form.elements.api_format.value = available[0];
  const defaults = preset.defaults && preset.defaults[form.elements.api_format.value];
  if (defaults) { form.elements.base_url.value = defaults.base_url || ''; form.elements.model.value = defaults.model || ''; }
  updateProviderHint();
}
async function loadSettings() {
  const data = await api('/api/config'); state.config = data.config; state.providerPresets = data.presets || state.providerPresets || {};
  state.defaultMode = data.config && data.config.mode || 'auto'; if (!runIsActive()) state.mode = state.defaultMode;
  const providerLabel = data.provider.enabled ? 'LLM 已启用' : data.provider.configured ? 'API 已配置 · 确定性' : '未配置 LLM';
  $('#provider-chip').textContent = providerLabel; $('#provider-chip').className = 'chip status-chip-button ' + (data.provider.enabled ? 'safe' : 'neutral'); $('#evidence-provider').textContent = data.provider.enabled ? '外部 API · 仅 SafeEvidence' : data.provider.configured ? '已配置 · 当前仍确定性' : '确定性降级';
  $('#settings-provider-state').textContent = providerLabel; $('#settings-provider-state').className = 'chip ' + (data.provider.enabled ? 'safe' : 'neutral');
  const storageLabels = { environment: '已配置 · 环境变量', 'os-keychain': '已配置 · 系统钥匙串', 'local-protected-file': '已配置 · 本机保护文件', not_configured: '未配置' };
  $('#secret-storage-status').textContent = storageLabels[data.config.secret_storage] || (data.config.api_key_configured ? '已配置' : '未配置');
  const form = $('#settings-form'); Object.entries(data.config || {}).forEach(function (entry) { if (form.elements[entry[0]] && entry[0] !== 'api_key') { if (form.elements[entry[0]].type === 'checkbox') form.elements[entry[0]].checked = Boolean(entry[1]); else form.elements[entry[0]].value = entry[1] == null ? '' : entry[1]; } });
  const environmentKey = data.config.secret_storage === 'environment';
  form.elements.api_key.value = ''; form.elements.api_key.disabled = environmentKey; form.elements.api_key.placeholder = environmentKey ? '由 RISK_AGENT_API_KEY 环境变量提供' : '留空表示不修改';
  form.elements.clear_api_key.checked = false; form.elements.clear_api_key.disabled = environmentKey; form.elements.clear_api_key.closest('label').classList.toggle('disabled-setting', environmentKey);
  $('#secret-clear-hint').textContent = environmentKey ? '环境变量中的密钥无法在页面删除，请在启动环境中移除 RISK_AGENT_API_KEY 后重启。' : '可在这里更新或清除本机钥匙串 / 保护文件中的密钥。';
  updateProviderHint();
  renderModeIndicator();
}
async function saveSettings(event) {
  event.preventDefault(); const formElement = event.target; const form = new FormData(formElement); const payload = Object.fromEntries(form.entries()); payload.llm_enabled = formElement.elements.llm_enabled.checked; payload.clear_api_key = formElement.elements.clear_api_key.checked; payload.run_token_budget = Number(payload.run_token_budget || 0); payload.monthly_token_budget = Number(payload.monthly_token_budget || 0);
  if (payload.clear_api_key && String(payload.api_key || '').trim()) { $('#settings-status').textContent = '更新密钥和清除密钥不能同时执行，请只选择一项。'; return; }
  if (payload.clear_api_key && !(await openDialog({ title: '清除 Provider API Key', message: '这会清除这台电脑上保存的密钥。环境变量中的密钥需要在启动环境中手动移除。', submitLabel: '确认清除', confirm: true }))) return;
  const submit = formElement.querySelector('button[type="submit"]'); submit.disabled = true; $('#settings-status').textContent = '正在保存本机设置…';
  try { await api('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); await loadSettings(); $('#settings-status').textContent = '设置已保存，仅对这台电脑生效。'; renderProject(); }
  catch (error) { $('#settings-status').textContent = error.message; }
  finally { submit.disabled = false; }
}
async function sendFeedback(reaction) {
  if (!state.run) return; const values = reaction === 'dislike' ? await openDialog({ title: '请补充改进建议', message: '你的反馈会帮助我们调整后续的 Agent 回复。', submitLabel: '提交反馈', fields: [{ name: 'reason', label: '哪里需要改进（可留空）', type: 'textarea', maxlength: 1000 }] }) : {};
  if (reaction === 'dislike' && !values) return;
  const reason = values && values.reason || '';
  await api('/api/runs/' + state.run.id + '/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reaction: reaction, reason: reason }) }); showNotice('反馈已记录，不会改变正式确认。');
}
async function sendConversation(event) {
  event.preventDefault();
  if (!state.project) return;
  const input = $('#conversation-input'); const message = input.value.trim();
  if (!message) return;
  const button = event.target.querySelector('button[type="submit"]'); button.disabled = true;
  try {
    const result = await api('/api/projects/' + state.project.id + '/conversation', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: message, run_id: state.run && state.run.id }) });
    state.conversation = result.conversation; state.messages.push(result.user_message, result.assistant_message); input.value = ''; renderConversation();
  } catch (error) { showNotice(error.message, 'block'); }
  finally { button.disabled = false; }
}
async function sendMessageFeedback(messageId, reaction) {
  if (!state.run || !messageId) return;
  const values = reaction === 'dislike' ? await openDialog({ title: '请补充改进建议', message: '请描述这条 Agent 回复哪里不够好。', submitLabel: '提交反馈', fields: [{ name: 'reason', label: '哪里需要改进（可留空）', type: 'textarea', maxlength: 1000 }] }) : {};
  if (reaction === 'dislike' && !values) return;
  const reason = values && values.reason || '';
  try { await api('/api/runs/' + state.run.id + '/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reaction: reaction, reason: reason, message_id: messageId }) }); showNotice('已记录这条 Agent 回复的反馈。'); }
  catch (error) { showNotice(error.message, 'block'); }
}
async function openSettingsView() {
  state.settingsReturnFocus = document.activeElement; setAppView('settings', true); $('#settings-status').textContent = '正在读取本机设置…';
  try { await loadSettings(); $('#settings-status').textContent = ''; }
  catch (error) { $('#settings-status').textContent = '设置读取失败：' + error.message; }
}

$('#new-project').addEventListener('click', createProject); $('#empty-new-project').addEventListener('click', createProject); $('#empty-demo').addEventListener('click', createDemo); $('#start-run').addEventListener('click', startRun); $('#choose-file').addEventListener('click', function () { $('#file-input').click(); }); $('#file-input').addEventListener('change', function (event) { uploadFile(event.target.files[0]); });
$('#archive-project').addEventListener('click', toggleArchiveProject); $('#delete-project').addEventListener('click', deleteProject);
$('#choose-dictionary').addEventListener('click', function () { $('#dictionary-input').click(); }); $('#dictionary-input').addEventListener('change', function (event) { uploadDictionary(event.target.files[0]); });
$('#run-analysis').addEventListener('click', runAnalysis);
$('#dataset-card').addEventListener('dragover', function (event) { event.preventDefault(); $('#dataset-card').classList.add('dragging'); });
$('#dataset-card').addEventListener('dragleave', function () { $('#dataset-card').classList.remove('dragging'); });
$('#dataset-card').addEventListener('drop', function (event) { event.preventDefault(); $('#dataset-card').classList.remove('dragging'); uploadFile(event.dataTransfer.files[0]); });
$('#open-settings').addEventListener('click', openSettingsView); $('#provider-chip').addEventListener('click', openSettingsView); $('#open-workspace').addEventListener('click', function () { setAppView('project'); }); $('#close-settings').addEventListener('click', function () { setAppView('project', true); }); $('#cancel-settings').addEventListener('click', async function () { await loadSettings(); $('#settings-status').textContent = '已恢复为上次保存的设置。'; }); $('#settings-form').addEventListener('submit', saveSettings);
document.querySelectorAll('[data-workspace-tab]').forEach(function (button) { button.addEventListener('click', function () { setWorkspaceTab(button.dataset.workspaceTab); }); });
$('.workspace-tabs').addEventListener('keydown', function (event) { const tabs = Array.from(document.querySelectorAll('[data-workspace-tab]')); const current = tabs.indexOf(document.activeElement); if (current < 0) return; let next = current; if (event.key === 'ArrowRight') next = (current + 1) % tabs.length; else if (event.key === 'ArrowLeft') next = (current - 1 + tabs.length) % tabs.length; else if (event.key === 'Home') next = 0; else if (event.key === 'End') next = tabs.length - 1; else return; event.preventDefault(); setWorkspaceTab(tabs[next].dataset.workspaceTab); tabs[next].focus(); });
document.querySelectorAll('[data-settings-target]').forEach(function (button) { button.addEventListener('click', function () { document.querySelectorAll('[data-settings-target]').forEach(function (item) { const selected = item === button; item.classList.toggle('active', selected); if (selected) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current'); }); const target = document.getElementById(button.dataset.settingsTarget); if (target) { target.scrollIntoView({ behavior: 'smooth', block: 'start' }); target.focus({ preventScroll: true }); } }); });
$('.settings-nav').addEventListener('keydown', function (event) { const items = Array.from(document.querySelectorAll('[data-settings-target]')); const current = items.indexOf(document.activeElement); if (current < 0) return; let next = current; if (event.key === 'ArrowDown') next = (current + 1) % items.length; else if (event.key === 'ArrowUp') next = (current - 1 + items.length) % items.length; else if (event.key === 'Home') next = 0; else if (event.key === 'End') next = items.length - 1; else return; event.preventDefault(); items[next].focus(); items[next].click(); });
document.querySelectorAll('.feedback-button').forEach(function (button) { button.addEventListener('click', function () { sendFeedback(button.dataset.reaction); }); });
$('#settings-form').elements.provider.addEventListener('change', applyProviderPreset); $('#settings-form').elements.api_format.addEventListener('change', applyProviderPreset);
$('#settings-form').elements.api_key.addEventListener('input', function () { if (this.value.trim()) $('#settings-form').elements.clear_api_key.checked = false; }); $('#settings-form').elements.clear_api_key.addEventListener('change', function () { if (this.checked) $('#settings-form').elements.api_key.value = ''; });
$('#interaction-dialog-submit').addEventListener('click', function () { closeDialog(readDialogFields()); }); $('#interaction-dialog-cancel').addEventListener('click', function () { closeDialog(null); }); $('#interaction-dialog-close').addEventListener('click', function () { closeDialog(null); }); $('#interaction-dialog').querySelector('[data-dialog-cancel]').addEventListener('click', function () { closeDialog(null); });
$('#provider-test').addEventListener('click', async function () {
  const testButton = $('#provider-test'); testButton.disabled = true; $('#settings-status').textContent = '正在测试当前表单配置…';
  const formElement = $('#settings-form'); const form = new FormData(formElement); const payload = Object.fromEntries(form.entries());
  try {
    const result = await api('/api/config/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: payload.provider, api_format: payload.api_format, base_url: payload.base_url, model: payload.model, proxy: payload.proxy, ca_cert: payload.ca_cert, api_key: payload.api_key || '' }) });
    $('#settings-status').textContent = result.ok ? '连接成功：' + result.provider + ' · ' + result.api_format + ' · ' + result.model : (result.error_code || '连接未成功') + '：' + result.message;
  } catch (error) { $('#settings-status').textContent = error.message; }
  finally { testButton.disabled = false; }
});
$('#conversation-form').addEventListener('submit', sendConversation);
async function bootstrap() {
  try { await loadSettings(); } catch (error) { $('#settings-status').textContent = '设置读取失败：' + error.message; }
  try { await loadProjects(); } catch (error) { showNotice(error.message, 'block'); }
}
bootstrap();
