# 风控建模 Agent

本地优先的消费信贷二分类建模工作台。产品面向懂 AUC、KS、IV 等风控指标、但不希望手写整套建模代码的业务建模人员。

公开仓库：[aaaaaaa-feng/risk-model-agent](https://github.com/aaaaaaa-feng/risk-model-agent)

## V1 能做什么

- 导入本机 CSV、XLSX/XLSM/XLS（含多 Sheet）和数据字典。
- 处理订单/客户粒度、多张特征表和多个 `0/1/-1/空值` Y；每个 Y 独立冻结有效样本并顺序运行。
- 提供四级关联兜底：Agent 推荐、可视化编辑、Agent 生成 Notebook、用户手写 Notebook；所有结果重新执行粒度、重复、样本膨胀、Y 与血缘检查。
- 依次执行诊断、清洗、Train/Test/OOT、IV/缺失率/相关性筛选、自动或人工分箱、候选训练、校准、Reviewer 质检、报告、模型包和批量评分。
- 支持 Dummy、WOE Logistic Scorecard、正则化 Logistic、Random Forest、Extra Trees、XGBoost、LightGBM、CatBoost；根据资源运行推荐组合，不默认全跑。
- 默认评分范围 300—900，高分代表低风险；基准分 600、基准好坏比 20:1、PDO 50，均可在确认节点调整。
- 由同一份结构化事实数据生成 Web、Excel、单文件 HTML 和模型包；评分结果列以模型版本名称命名。
- 提供半信任与完全信任两种模式。Reviewer 在独立上下文审核计划、生成代码、执行证据和报告，最多三轮修复后进入受控降级；安全阻断不能被自动批准。
- SSE 持续输出阶段、节点、Agent、工具、状态、摘要、时间和证据引用；不输出隐藏思维链。

## 数据安全边界

- 原始表、客户级记录、逐行预测和模型文件保存在应用专属本地目录，不由产品上传到外部 LLM。
- DeepSeek、Kimi、Kimi Code、OpenAI、Anthropic 与自定义 Provider 只能接收通过 DLP 的聚合 `SafeEvidence`；小于 30 个样本的分组被抑制。
- API Key 优先使用系统凭据存储，失败时使用权限受限的本地密钥文件；页面不回显密钥，并可在保存前测试当前表单连接。
- Notebook 使用项目级本地 Kernel，默认允许联网。它不是安全沙箱；用户代码和第三方包可能主动外发数据，关闭产品侧 LLM 外发不能替代操作系统网络隔离。
- 项目迁移包使用 AES-256-GCM；密码经 scrypt 派生，并支持独立恢复密钥。

## 架构

- React + TypeScript + Vite：四区专业建模台（项目列表、主工作区、阶段栏、常驻 Agent 对话）。
- Python 3.11+、FastAPI、SQLite：本地 API、领域数据和不可变数据版本。
- LangGraph：暂停、恢复、Human in the Loop、Reviewer 循环与 SQLite checkpoint。
- 本地 Worker：确定性数据、统计、建模、报告和评分工具；Worker 不是 Agent。
- 强类型 Tool Registry：V1 不开放任意 MCP 工具发现，只保留未来适配边界。

## 本地开发

需要 Python 3.11—3.13、Node.js 22+。

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cd frontend
npm ci
npm run build
cd ..
RISK_AGENT_OPEN_BROWSER=0 .venv/bin/python run_local.py
```

打开 `http://127.0.0.1:8765/`。不配置 LLM 时，系统明确显示“本地降级”，确定性 Worker 和本地 Reviewer 门禁仍可运行。

Windows PowerShell 使用 `.venv\Scripts\python.exe`，其余步骤一致。

## 验证

```bash
.venv/bin/ruff check app tests scripts
.venv/bin/python -m pytest
.venv/bin/python scripts/run_golden_cases.py
cd frontend && npm run typecheck && npm run build
```

打包前检查与本机 macOS 构建：

```bash
.venv/bin/python -m pip install -e ".[package]"
.venv/bin/python scripts/verify_packaging.py
./scripts/build_mac.sh
```

Windows 使用 `scripts/build_windows.ps1`。GitHub Actions 同时构建 macOS 13+ Apple Silicon 与 Windows 10/11 x64 候选产物，但在签名安装和真实目标机验收完成前，不应宣传为正式安装版。

## 本地数据与升级

- macOS：`~/Library/Application Support/RiskModelAgent`
- Windows：`%LOCALAPPDATA%\RiskModelAgent`
- Linux 开发环境：`$XDG_DATA_HOME/risk-model-agent` 或 `~/.local/share/risk-model-agent`

首次从旧版启动时，系统先快照旧数据库，再复制完整旧运行目录；可兼容的项目/数据版本迁入 V1，不兼容 Run 作为只读记录保留。源数据不会由迁移器静默删除。

## 正式规格

`docs/01`—`docs/10` 是 V1 的正式产品与研发规格；原始访谈基线保存在 `docs/archive/13-需求访谈决策基线.md`，仅用于追溯。独立评测 Harness、多用户权限、内网多人部署、代码签名和生产部署验收均属于后续项目，不伪装成 V1 已交付能力。

## License

[MIT](LICENSE)
