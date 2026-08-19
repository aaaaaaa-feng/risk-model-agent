const state = { projects: [], project: null, datasets: [], dictionaries: [], runs: [], run: null, events: [], mode: 'auto', eventSource: null, config: null };
const $ = function (selector) { return document.querySelector(selector); };
const projectList = $('#project-list');
const escapeHtml = function (value) { return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[char]; }); };

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
function renderProjects() {
  projectList.innerHTML = state.projects.length ? state.projects.map(function (project) {
    return '<button class="project-item ' + (state.project && state.project.id === project.id ? 'active' : '') + '" data-project-id="' + escapeHtml(project.id) + '"><strong>' + escapeHtml(project.name) + '</strong><small>' + escapeHtml(project.status) + '</small></button>';
  }).join('') : '<div class="muted" style="padding:10px">还没有项目</div>';
  projectList.querySelectorAll('[data-project-id]').forEach(function (button) { button.addEventListener('click', function () { selectProject(button.dataset.projectId); }); });
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
  state.project = data.project; state.datasets = data.datasets || []; state.dictionaries = data.dictionaries || []; state.runs = data.runs || []; state.run = state.runs[0] || null; state.mode = state.run ? state.run.mode : state.mode; state.events = [];
  renderProjects(); renderProject();
  if (state.run) {
    const eventData = await api('/api/runs/' + state.run.id + '/events');
    state.events = eventData.events || []; renderTimeline(); connectStream();
    if (state.run.status === 'succeeded') {
      try { renderReport(await api('/api/runs/' + state.run.id + '/report')); } catch (error) { /* report may still be flushing */ }
    }
  }
}
function renderProject() {
  const hasProject = Boolean(state.project);
  $('#empty-state').classList.toggle('hidden', hasProject); $('#workspace').classList.toggle('hidden', !hasProject);
  if (!hasProject) return;
  $('#project-title').textContent = state.project.name;
  $('#dataset-version').textContent = state.datasets.length ? 'v' + state.datasets.length : '—';
  $('#run-version').textContent = state.run ? state.run.id.slice(-6) : '—';
  renderDataset(); renderDictionaryStatus(); renderRun(); renderStages(); renderAnalysisControls();
  $('#start-run').disabled = !state.datasets.length || ['queued', 'running', 'paused', 'awaiting_confirmation'].includes(state.run && state.run.status);
}
function renderDataset() {
  const dataset = state.datasets[0]; const card = $('#dataset-card');
  if (!dataset) {
    card.className = 'dataset-card empty-panel';
    card.innerHTML = '<div class="drop-icon">↑</div><strong>拖入 CSV / XLSX</strong><span>文件只写入本机项目目录</span><button id="choose-file" class="secondary-button small">选择文件</button>';
    $('#dataset-badge').textContent = '未导入'; $('#choose-file').addEventListener('click', function () { $('#file-input').click(); }); return;
  }
  card.className = 'dataset-card dataset-filled';
  const resource = dataset.profile && dataset.profile.resource_estimate || {};
  const resourceNote = resource.risk === 'warn' ? '<br/><span class="warn-text">资源预估接近本机预算</span>' : resource.risk === 'block' ? '<br/><span class="error-text">资源预估超过当前边界</span>' : '';
  card.innerHTML = '<div class="file-title"><span class="drop-icon">✓</span>' + escapeHtml(dataset.filename) + '</div><div class="file-meta">大小 ' + (dataset.bytes / 1024).toFixed(1) + ' KB<br/>' + (dataset.rows || '—') + ' 行 · ' + (dataset.columns || '—') + ' 列<br/>SHA-256 ' + escapeHtml(dataset.sha256.slice(0, 16)) + '…' + resourceNote + '</div><button id="replace-file" class="secondary-button small" type="button">重新导入版本</button>';
  $('#replace-file').addEventListener('click', function () { $('#file-input').click(); });
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
  $('#current-node').textContent = event && event.payload && event.payload.node ? phaseLabel(event.payload.node) : (status === 'idle' ? '等待导入数据' : phaseLabel(run.phase));
  $('#current-message').textContent = event && event.payload && event.payload.message ? event.payload.message : statusText(status);
  $('#inspector-status').textContent = statusLabel(status); $('#inspector-status').className = 'chip ' + statusClass(status);
  $('#run-mode').textContent = state.mode === 'semi_trust' ? '半信任模式' : '自动运行';
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
    return '<div class="stage-item ' + (done ? 'done' : '') + ' ' + (active ? 'active' : '') + ' ' + (blocked ? 'blocked' : '') + '">' + (done ? '✓ ' : '') + item[1] + '</div>';
  }).join('');
}
function renderAnalysisControls() {
  const control = $('#analysis-controls'); const button = $('#run-analysis');
  if (!control || !button) return;
  const profile = state.run && state.run.state && state.run.state.profile || (state.datasets[0] && state.datasets[0].profile) || {};
  const columns = profile.columns_detail || [];
  if (!columns.length) { control.innerHTML = '<span class="muted">开始一次分析后，这里会出现可选字段。</span>'; button.disabled = true; return; }
  const options = columns.map(function (item) { return '<option value="' + escapeHtml(item.name) + '">' + escapeHtml(item.name) + ' · ' + escapeHtml(item.type || '') + '</option>'; }).join('');
  control.innerHTML = [0, 1, 2, 3].map(function (index) { return '<select class="analysis-select" data-analysis-index="' + index + '"><option value="">维度 ' + (index + 1) + '（可选）</option>' + options + '</select>'; }).join('');
  control.querySelectorAll('.analysis-select').forEach(function (select) { select.addEventListener('change', function () { button.disabled = !Array.from(control.querySelectorAll('.analysis-select')).some(function (item) { return item.value; }); }); });
  button.disabled = true;
}
async function runAnalysis() {
  if (!state.project || !state.datasets[0]) return;
  const selects = Array.from(document.querySelectorAll('.analysis-select')).map(function (select) { return select.value; }).filter(Boolean);
  if (!selects.length) return;
  const profile = state.run && state.run.state && state.run.state.profile || {};
  const details = Object.fromEntries((profile.columns_detail || []).map(function (item) { return [item.name, item]; }));
  try {
    const result = await api('/api/projects/' + state.project.id + '/analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset_id: state.datasets[0].id, spec: { dimensions: selects.map(function (column) { return { column: column, transform: details[column] && details[column].type === 'numeric' ? 'quantile_bins' : 'category', bins: 10 }; }), target: { column: state.run && state.run.state && state.run.state.plan && state.run.state.plan.target }, min_group_size: 50, max_groups: 1000 } }) });
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
    const baselineCandidates = profileColumns.filter(function (item) { return item.type === 'numeric' && item.name !== (plan.target || target.target); });
    const baselineOptions = ['<option value="">不导入既有模型基线</option>'].concat(baselineCandidates.map(function (item) { return '<option value="' + escapeHtml(item.name) + '" ' + (item.name === plan.baseline_column ? 'selected' : '') + '>' + escapeHtml(item.name) + '</option>'; })).join('');
    const targetOptions = targetCandidates.map(function (candidate) { return '<option value="' + escapeHtml(candidate) + '" ' + (candidate === (plan.target || target.target) ? 'selected' : '') + '>' + escapeHtml(candidate) + '</option>'; }).join('');
    const splitOptions = ['time_holdout', 'stratified_holdout'].map(function (method) { return '<option value="' + method + '" ' + (method === (split.method || 'stratified_holdout') ? 'selected' : '') + '>' + (method === 'time_holdout' ? '时间留出' : '分层留出') + '</option>'; }).join('');
    const modelOptions = ['woe_logistic_scorecard', 'logistic_regression', 'random_forest', 'hist_gradient_boosting', 'xgboost'].map(function (model) { return '<label class="model-option"><input type="checkbox" data-confirm-model="' + model + '" ' + ((plan.models || []).includes(model) ? 'checked' : '') + ' />' + model + '</label>'; }).join('');
    const findings = (review.findings || []).map(function (item) { return '<li>' + escapeHtml(item.message || item.code || '需关注') + '</li>'; }).join('');
    const cleaningFindings = (cleaning.requires_confirmation || []).map(function (item) { return '<li>' + escapeHtml(item.message || item.code || '需关注') + '</li>'; }).join('');
    const cleaningButton = run.phase === 'cleaning' && (cleaning.requires_confirmation || []).length && !cleaning.execution ? '<button class="secondary-button action-button" id="apply-cleaning">批准并生成新数据版本</button>' : '';
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
      '<div><span class="control-label">本次候选模型</span><div class="model-options">' + modelOptions + '</div></div></div>' +
      cleaningButton + '<button class="primary-button action-button" id="confirm-plan">确认并继续</button>';
    $('#confirm-plan').addEventListener('click', confirmPlan);
    if ($('#apply-cleaning')) $('#apply-cleaning').addEventListener('click', applyCleaning);
  }
  else if (run.status === 'succeeded') { card.innerHTML = '<h3>本次 Run 已完成</h3><p>模型、代码交付物和报告已保存在本机。你可以打开 HTML 报告，或从当前方案派生隔离的 what-if 实验。</p><a class="secondary-button action-button" href="/api/runs/' + run.id + '/report.html" target="_blank">打开报告</a><div class="action-links"><a class="text-link" href="/api/runs/' + run.id + '/report.xlsx" target="_blank">下载 XLSX</a><a class="text-link" href="/api/runs/' + run.id + '/trace.zip" target="_blank">下载 Trace</a></div><button class="secondary-button action-button" id="what-if-run">派生 what-if 实验</button>'; $('#what-if-run').addEventListener('click', createWhatIf); }
  else if (run.status === 'paused') { card.innerHTML = '<h3>Run 已暂停</h3><p>已保存最近安全节点，不会把半成品当作成功结果。</p><button class="primary-button action-button" id="resume-run">恢复运行</button><button class="secondary-button action-button" id="cancel-run">取消 Run</button>'; $('#resume-run').addEventListener('click', resumeRun); $('#cancel-run').addEventListener('click', cancelRun); }
  else if (run.status === 'queued' || run.status === 'running') { card.innerHTML = '<h3>正在运行</h3><p>页面会持续接收节点和工具事件。你可以暂停到最近安全节点，或取消本次 Run。</p><button class="secondary-button action-button" id="pause-run">暂停</button><button class="secondary-button action-button" id="cancel-run">取消 Run</button>'; $('#pause-run').addEventListener('click', pauseRun); $('#cancel-run').addEventListener('click', cancelRun); }
  else if (run.status === 'failed' || run.status === 'blocked') card.innerHTML = '<h3>需要处理</h3><p>' + escapeHtml(run.error || '查看时间线中的结构化问题。') + '</p>';
  else if (run.status === 'cancelled') card.innerHTML = '<h3>Run 已取消</h3><p>未完成产物已与正式结果隔离。可以修改方案后新建一次 Run。</p>';
  else card.innerHTML = '<h3>正在运行</h3><p>页面会持续接收节点和工具事件。可以离开后回来继续查看。</p>';
}
function renderReport(report) {
  if (!report) return; $('#report-panel').classList.remove('hidden'); $('#report-link').href = '/api/runs/' + state.run.id + '/report.html';
  const champion = report.champion || {}; const metrics = champion.validation || {};
  const calibration = metrics.calibration || []; const stability = report.stability && report.stability.features || [];
  const values = [['冠军建议', champion.name || '—'], ['验证 ROC-AUC', metrics.roc_auc == null ? '—' : metrics.roc_auc], ['验证 KS', metrics.ks == null ? '—' : metrics.ks], ['最终变量', report.selection && report.selection.funnel ? report.selection.funnel.final : '—']];
  $('#metric-grid').innerHTML = values.map(function (item) { return '<div class="metric-card"><span>' + item[0] + '</span><strong>' + escapeHtml(item[1]) + '</strong></div>'; }).join('');
  $('#report-narrative').textContent = report.narrative || '报告已生成。';
  const decisions = report.selection && report.selection.decisions || [];
  const excluded = decisions.filter(function (item) { return item.status !== 'included'; });
  const calibrationGap = calibration.length ? Math.max.apply(null, calibration.map(function (item) { return Number(item.absolute_gap || 0); })).toFixed(4) : '—';
  const highPsi = stability.filter(function (item) { return ['review', 'high'].includes(item.validation && item.validation.review_flag) || ['review', 'high'].includes(item.oot && item.oot.review_flag); }).length;
  $('#report-details').innerHTML = '<div class="report-facts"><span>校准最大绝对差</span><strong>' + calibrationGap + '</strong><span>需稳定性复核变量</span><strong>' + highPsi + '</strong></div>' + (excluded.length ? '<strong>字段处理摘要</strong><ul>' + excluded.map(function (item) { return '<li><code>' + escapeHtml(item.column) + '</code> · ' + escapeHtml(item.status) + ' · ' + escapeHtml((item.reasons || []).join('、') || '规则排除') + '</li>'; }).join('') + '</ul>' : '<strong>字段处理摘要</strong><span>没有字段被规则排除。</span>');
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
  if (state.run.status === 'succeeded') { const report = await api('/api/runs/' + state.run.id + '/report'); renderReport(report); }
}
async function createProject() {
  const name = window.prompt('项目名称', '我的风控建模项目'); if (!name) return;
  const data = await api('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name }) });
  state.project = data.project; await loadProjects(); await selectProject(data.project.id);
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
    if (inspection.requires_sheet) { const sheet = window.prompt('请选择 Sheet：' + inspection.sheets.join('、'), inspection.sheets[0]); if (!sheet) return; form.append('sheet', sheet); }
    const data = await api('/api/projects/' + state.project.id + '/datasets', { method: 'POST', body: form }); showNotice(data.message); await selectProject(state.project.id);
  }
  catch (error) { showNotice(error.message, 'block'); }
}
async function uploadDictionary(file) {
  if (!state.project || !file) return;
  const form = new FormData(); form.append('file', file);
  try {
    const result = await api('/api/projects/' + state.project.id + '/dictionaries', { method: 'POST', body: form });
    showNotice(result.message || '数据字典已保存到本机。');
    await selectProject(state.project.id);
  } catch (error) { showNotice(error.message, 'block'); }
}
async function startRun() {
  if (!state.project || !state.datasets[0]) return;
  try {
    const data = await api('/api/projects/' + state.project.id + '/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset_id: state.datasets[0].id, mode: state.mode }) });
    state.run = data.run; state.events = []; renderProject(); connectStream();
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
    await api('/api/runs/' + state.run.id + '/decision', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'plan_confirmation', values: { target: target, split_method: splitMethod, models: models, baseline_column: baselineColumn || null, reviewed_without_code_reading: true } }) });
    showNotice('已记录正式确认，继续本地筛选与训练。'); await syncRun(); connectStream();
  } catch (error) { showNotice(error.message, 'block'); }
}
async function applyCleaning() {
  if (!state.run) return;
  const cleaning = state.run.state && state.run.state.cleaning || {};
  const actions = (cleaning.requires_confirmation || []).map(function (item) { return { code: item.code, columns: item.columns || [] }; });
  if (!actions.length || !window.confirm('确认按当前方案执行清洗？系统会保留原数据并创建新的本地数据版本。')) return;
  try { await api('/api/runs/' + state.run.id + '/clean', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ actions: actions }) }); showNotice('清洗已执行并生成新的本地数据版本。'); await syncRun(); } catch (error) { showNotice(error.message, 'block'); }
}
async function createWhatIf() {
  if (!state.project || !state.run) return;
  const currentPlan = state.run.state && state.run.state.plan || {};
  const currentScreening = currentPlan.screening || {};
  const rawIv = window.prompt('what-if：新的最小 IV 阈值（留空表示沿用当前值）', String(currentScreening.min_iv == null ? 0.005 : currentScreening.min_iv));
  if (rawIv === null) return;
  const minIv = Number(rawIv);
  if (!Number.isFinite(minIv) || minIv < 0 || minIv > 10) { showNotice('IV 阈值必须是 0—10 的数值。', 'block'); return; }
  try {
    const result = await api('/api/projects/' + state.project.id + '/what-if', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_run_id: state.run.id, changes: { min_iv: minIv } }) });
    showNotice('what-if 实验已隔离启动，不会覆盖正式 Run。');
    state.run = result.run; state.events = []; renderProject(); connectStream();
  } catch (error) { showNotice(error.message, 'block'); }
}
async function pauseRun() { if (!state.run) return; try { await api('/api/runs/' + state.run.id + '/pause', { method: 'POST' }); await syncRun(); if (state.eventSource) state.eventSource.close(); } catch (error) { showNotice(error.message, 'block'); } }
async function resumeRun() { if (!state.run) return; try { await api('/api/runs/' + state.run.id + '/resume', { method: 'POST' }); await syncRun(); connectStream(); } catch (error) { showNotice(error.message, 'block'); } }
async function cancelRun() { if (!state.run || !window.confirm('确认取消当前 Run？未完成产物不会作为正式结果。')) return; try { await api('/api/runs/' + state.run.id + '/cancel', { method: 'POST' }); await syncRun(); if (state.eventSource) state.eventSource.close(); } catch (error) { showNotice(error.message, 'block'); } }
async function loadSettings() {
  const data = await api('/api/config'); state.config = data.config; $('#provider-chip').textContent = data.provider.enabled ? 'LLM 已启用' : data.provider.configured ? 'API 已配置 · 确定性' : '未配置 LLM'; $('#provider-chip').className = 'chip ' + (data.provider.enabled ? 'safe' : 'neutral'); $('#evidence-provider').textContent = data.provider.enabled ? '外部 API · 仅 SafeEvidence' : data.provider.configured ? '已配置 · 当前仍确定性' : '确定性降级';
  const form = $('#settings-form'); Object.entries(data.config || {}).forEach(function (entry) { if (form.elements[entry[0]] && entry[0] !== 'api_key') { if (form.elements[entry[0]].type === 'checkbox') form.elements[entry[0]].checked = Boolean(entry[1]); else form.elements[entry[0]].value = entry[1] == null ? '' : entry[1]; } });
}
async function saveSettings(event) {
  event.preventDefault(); const formElement = event.target; const form = new FormData(formElement); const payload = Object.fromEntries(form.entries()); payload.llm_enabled = formElement.elements.llm_enabled.checked; payload.run_token_budget = Number(payload.run_token_budget || 0); payload.monthly_token_budget = Number(payload.monthly_token_budget || 0);
  try { await api('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); $('#settings-status').textContent = '已保存到本机安全目录。'; await loadSettings(); setTimeout(function () { $('#settings-modal').classList.add('hidden'); }, 700); }
  catch (error) { $('#settings-status').textContent = error.message; }
}
async function sendFeedback(reaction) {
  if (!state.run) return; const reason = reaction === 'dislike' ? window.prompt('请告诉我们哪里需要改进（可留空）', '') : '';
  await api('/api/runs/' + state.run.id + '/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reaction: reaction, reason: reason }) }); showNotice('反馈已记录，不会改变正式确认。');
}

$('#new-project').addEventListener('click', createProject); $('#empty-new-project').addEventListener('click', createProject); $('#empty-demo').addEventListener('click', createDemo); $('#start-run').addEventListener('click', startRun); $('#choose-file').addEventListener('click', function () { $('#file-input').click(); }); $('#file-input').addEventListener('change', function (event) { uploadFile(event.target.files[0]); });
$('#choose-dictionary').addEventListener('click', function () { $('#dictionary-input').click(); }); $('#dictionary-input').addEventListener('change', function (event) { uploadDictionary(event.target.files[0]); });
$('#run-analysis').addEventListener('click', runAnalysis);
$('#dataset-card').addEventListener('dragover', function (event) { event.preventDefault(); $('#dataset-card').classList.add('dragging'); });
$('#dataset-card').addEventListener('dragleave', function () { $('#dataset-card').classList.remove('dragging'); });
$('#dataset-card').addEventListener('drop', function (event) { event.preventDefault(); $('#dataset-card').classList.remove('dragging'); uploadFile(event.dataTransfer.files[0]); });
$('#mode-toggle').addEventListener('click', function () { state.mode = state.mode === 'auto' ? 'semi_trust' : 'auto'; $('#mode-toggle').textContent = state.mode === 'auto' ? '自动运行模式' : '半信任模式'; renderRun(); });
$('#open-settings').addEventListener('click', async function () { $('#settings-modal').classList.remove('hidden'); await loadSettings(); }); $('#close-settings').addEventListener('click', function () { $('#settings-modal').classList.add('hidden'); }); $('#cancel-settings').addEventListener('click', function () { $('#settings-modal').classList.add('hidden'); }); $('#settings-form').addEventListener('submit', saveSettings);
document.querySelectorAll('.feedback-button').forEach(function (button) { button.addEventListener('click', function () { sendFeedback(button.dataset.reaction); }); });
$('#provider-test').addEventListener('click', async function () { $('#settings-status').textContent = '正在测试…'; try { const result = await api('/api/config/test', { method: 'POST' }); $('#settings-status').textContent = result.ok ? '连接成功。' : (result.error_code || '连接未成功') + '：' + result.message; } catch (error) { $('#settings-status').textContent = error.message; } });
loadSettings().catch(function () {}); loadProjects().catch(function (error) { showNotice(error.message, 'block'); });
