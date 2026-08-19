# 风控建模 Agent

当前仓库包含可在本机启动的 V0.1 风控建模 Agent 工作台，以及 1—12 份产品与研发基线文档。

2026-08-20 已清除上一版原型的源码、运行数据库和旧产品文档，并依据最新讨论重新生成 1—10 项 AI 产品经理产出。上一版受 Git 跟踪的内容仍可从提交 `63349c0` 恢复，但不再作为当前方案依据。

## 已确认的产品方向

- 面向风控建模人员，V1 只处理 Y 为 0/1 的二分类任务。
- 支持 CSV、XLSX；原始数据、客户级记录、模型文件和逐行预测不上传云端。
- 外部 LLM API 只接收经过本地脱敏与聚合的安全证据。
- LangGraph 负责有状态编排；不采用完整 LangChain 全家桶，也不以 Pi Agent 作为产品主运行时。
- 主 Agent、分析/代码 Agent 与 Reviewer Agent 通过结构化消息形成“生成—审核—修复—重审”闭环。
- 本地 Worker 是确定性 Python 工具，不是 Agent；负责画像、清洗、变量筛选、训练、指标和报告。
- 支持自动运行模式（对应讨论中的“完全信任模式”）与半信任模式；两种模式都不能绕过安全阻断。
- Web 界面不是一问一答聊天框，而是流程工作台、对话、证据和产物的组合。
- 当前产品只保留可导出的 Trace 与接口；独立评测 Harness 平台后置建设。
- 目标交付包含 macOS 与 Windows 可安装版本，但必须分别完成真实打包与验收后才能宣称支持。

## 1—10 产品文档

1. [产品需求文档 PRD](docs/01-产品需求文档-PRD.md)
2. [用户流程与状态机](docs/02-用户流程与状态机.md)
3. [Agent 角色与协作协议](docs/03-Agent角色与协作协议.md)
4. [Worker 与工具合约](docs/04-Worker与工具合约.md)
5. [数据安全与运行边界](docs/05-数据安全与运行边界.md)
6. [UI/UX 交互规格](docs/06-UI-UX交互规格.md)
7. [模型产出与报告规格](docs/07-模型产出与报告规格.md)
8. [验收标准与测试场景](docs/08-验收标准与测试场景.md)
9. [Trace 与后续评测接入协议](docs/09-Trace与后续评测接入协议.md)
10. [研发路线图与 Backlog](docs/10-研发路线图与Backlog.md)

11. [产品文档基线审计意见](docs/11-产品文档审计意见.md)（外部审阅输入）
12. [产品优化建议](docs/12-产品优化建议.md)（外部审阅输入）

## 建议阅读顺序

产品、设计和研发共同先阅读 01、02、03、04、05；前端重点阅读 06；算法与报告开发重点阅读 07；测试重点阅读 08；未来评测平台开发阅读 09；项目排期与实施以 10 为准。11、12 是已吸收的审阅输入，用于追溯本轮修订依据。

## 当前已实现与边界

- 已实现：本地 FastAPI Web 服务、CSV/XLSX 导入（多 Sheet 需明确选择、导入前行列/内存预算预估）、数据画像与本地 EDA、Y 契约检查、半信任确认卡（清洗动作不可绕过、可明确跳过）、LangGraph 状态编排、训练集范围内 IV/缺失/ID/泄漏筛选、报告内可检索/筛选/排序的变量明细和隔离 what-if、既有评分列新 OOT 复评（冻结验证方向与阈值）、数据字典版本化与字段语义联动、WOE + Logistic 评分卡、Logistic/Random Forest/HistGradientBoosting/XGBoost 候选比较、校准分箱/PSI/训练集相关性/变量重要性、HTML/JSON/XLSX/Python/哈希清单交付物、SSE 进度与可下载 Trace、项目备份与安全恢复、项目级多轮对话与消息反馈、报告叙事 Agent 草稿/专家编辑锁定、API 配置入口、Provider 出站脱敏摘要、多维 1—4 维本地分析。
- 已实现：Provider Gateway 的 OpenAI-compatible 接口和显式 `llm_enabled` 开关。外部请求只允许别名化 SafeEvidence，并在本地记录可查看的脱敏出站请求摘要/哈希；计划、代码审核失败会保留结构化原因并阻断；代码 Reviewer 使用 AST/依赖/危险调用静态门禁；项目对话外发只允许本地识别出的结构化意图，疑似凭据、邮箱、手机号/卡号或无法归类的自由文本留在本机；单 Run/单月 token 预算在本地调用前熔断并记录 usage。没有 Provider 时仍走确定性本地流程。
- 已实现：类别不平衡采用算法级处理而非重采样：Logistic/Random Forest/HistGradientBoosting 使用类别权重，XGBoost 使用训练分区计算的 `scale_pos_weight`；训练正负样本数、策略、`fit_scope=train` 和 `resampling=none` 写入 JSON/HTML/XLSX 报告，验证集与 OOT 不参与权重拟合。
- 已验证：本地单元、集成和端到端测试；变量筛选的 IV、WOE 和 OOF 诊断只从训练分区拟合（OOF 按折重新拟合 WOE）；生成代码只作为交付物，不在产品内执行；Reviewer 阻断不会被标成成功；JSON、HTML、XLSX 由同一次 Run 产物生成并写入 checksums；批准的去重/异常值动作会创建新的本地数据版本；Baseline 会在同一冻结样本上输出固定通过率和 swap set 聚合比较；what-if 会隔离为实验 Run。
- 尚未宣称：真实供应商 API 的生产连通性/费用验证、超参数搜索、macOS/Windows 安装包的真实构建与跨平台安装验收、OS Keychain/Credential Manager 在目标机器上的真实验收、XLSX 大文件的目标规模实测和独立评测 Harness。当前资源估算是导入前保护性边界，不等于 8GB 机器的最终支持规模；PSI、相关性和树模型变量重要性已实现为复核证据，不等于自动通过或生产稳定性结论。可选 `.[secure]` 依赖会优先使用系统凭据存储，失败时回退到 600 权限本地文件。训练分区提供受资源上限约束的 3-fold OOF 诊断，但冠军仍只按冻结验证集选择。Reviewer 现在支持最多三轮“冻结方案 → 受控模板重生成 → 重审”，但不是任意自然语言代码的语义修复器。

## 本机运行

```bash
python3.9 -m venv .venv
.venv/bin/pip install ".[dev]" --no-build-isolation
.venv/bin/python -m app.main
```

浏览器打开 `http://127.0.0.1:8765`。如果暂时不配置外部 Provider，页面会明确显示“确定性降级”，仍可使用本地 Worker 完成演示流程。

服务默认只绑定本机回环地址；只有在明确设置 `RISK_AGENT_ALLOW_REMOTE=1` 时才允许非回环绑定。远程绑定会扩大本地数据服务的暴露面，应由部署方自行配置认证、网络隔离和访问控制。

测试命令：

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check app tests
node --check app/static/app.js
.venv/bin/python scripts/run_golden_cases.py
```
