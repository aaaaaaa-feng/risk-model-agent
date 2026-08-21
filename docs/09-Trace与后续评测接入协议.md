# 09｜Trace 与后续评测接入协议

## 1. 当前范围与事实边界

V1.1 在 V1 的可评测接口之上，交付了一个单机、文件存储的独立评测 Harness 基础；它不是多人评测后台，也不接收原始数据。当前实现包括：

- 不可变 Run Manifest；
- Run/Tool/Reviewer/Provider/Human Gate 层级 Span；
- 脱敏 Trace Bundle；
- 可由 Python 直接调用的隔离 Target Adapter；
- Fake Provider、Worker 错误/超时和 Human Gate 决策注入；
- Baseline/Candidate Manifest 可比性检查。
- 版本化 Suite/Case/Trial 注册、Core/Edge/Safety/Recovery/Bad Case 分类；
- Outcome、Trajectory、Risk、Efficiency 四类确定性摘要和可配置门禁；
- 本地异步 API、同步 CLI、运行结果和 Trace Bundle 的原子落盘。

当前没有交付多人评测后台、排行榜、真正隔离的 Holdout 权限、Nightly 真实 API 调度、LLM Judge 校准或线上 Bad Case 管理平台。合成案例通过只证明机制和固定流程，不证明真实 Provider 能力、业务模型效果或生产稳定性。

当前 Trace Contract 以“一次建模 Run”为边界。项目对话仍使用可重连的 Conversation Event，尚未纳入同一 Target Adapter 和 Trace Bundle；不将这部分写成已交付。

## 2. Run Manifest

每个 Run 创建时冻结 `risk-agent-eval-manifest/v1`，记录：

- Git SHA、源码树 SHA-256，以及冻结应用的可执行文件 SHA-256；未提交代码或安装包不会只依赖 Git SHA 表示；
- Agent Graph、Prompt ID/版本/内容哈希；
- Reviewer Rubric、SafeEvidence 和错误分类版本；
- Tool 名称、版本、输入 Schema 哈希和整体 Tool Manifest 哈希；
- Provider、主模型、Reviewer 模型和非敏感参数；
- 数据版本、内容哈希、行列数、Y 名称哈希和标签配置哈希；
- Python、操作系统、架构和关键依赖版本；
- 评测时的 Suite、Case、Trial、Case 配置哈希和 Evaluator 版本。

Manifest 存入 `run_manifests`，同一 `run_id` 只能写入一次。接口：

```text
GET /api/v1/runs/{run_id}/manifest
```

`compare_manifests()` 会逐项检查核心运行条件。条件不同时必须标为不可直接归因，不能把全部差异宣传成模型或 Prompt 提升。

## 3. Trace Contract V1

一个 Run 对应一个 `trace_id` 和一个根 Span。子 Span 使用 `parent_span_id` 形成调用关系。

### 3.1 Span 类型

- `run`：端到端 Run；
- `tool`：确定性 Worker 工具；
- `reviewer`：独立审核记录；
- `llm`：一次 Provider 请求；
- `gate`：Human-in-the-Loop 或完全信任模式自动确认。

### 3.2 Span 状态

只允许：

- `requested`；
- `running`；
- `succeeded`；
- `failed`；
- `blocked`；
- `cancelled`。

Span 记录开始/结束时间、耗时、Agent、节点、工具、输入/输出哈希、标准错误、尝试次数、降级路径、Token/请求 ID、安全状态和证据引用。它不记录隐藏思维链、原始客户行或逐行预测。

### 3.3 SSE 事件

现有事件继续兼容，并新增：

```json
{
  "trace_id": "trace_xxx",
  "span_id": "span_xxx",
  "parent_span_id": "span_parent",
  "duration_ms": 125
}
```

事件摘要是可展示结论，不是模型私有推理。客户端继续按 `sequence` 去重和断线续传。

## 4. Reviewer 状态语义

不得再把不同来源统一显示为 `pass`：

- `deterministic_pass`：仅确定性规则通过；
- `llm_reviewer_pass`：LLM Reviewer 实际执行且通过；
- `fallback_pass`：Provider 关闭或失败，确定性审核兜底通过；
- `conditional_pass`：无阻断，但有明确条件或提醒；
- `revise`：需要修复并复审；
- `blocked`：不可继续的问题。

报告和界面必须保留来源。`fallback_pass` 不能宣传为“LLM 已审核”。

## 5. Provider 请求终态

建模 Run 内每个实际创建的 Provider 请求必须结束为 `succeeded/failed/blocked/cancelled`。记录安全 payload 哈希、Provider Request ID、HTTP 状态、标准错误、响应哈希、耗时、Token 和对应 Span；不记录 API Key 或完整响应正文。

DLP 和预算阻断使用脱敏占位证据创建请求记录，再以 `blocked` 结束。JSON Schema 解析失败只结算一次最终状态，不先写成功再重复写失败。

## 6. Target Adapter

稳定入口位于 `app.evaluation.adapter.run_eval_case`：

```python
from pathlib import Path
from app.evaluation.adapter import run_eval_case

result = run_eval_case(
    case={
        "case_id": "core_split_001",
        "suite_version": "risk-agent-eval/v1",
        "fixture": "synthetic_time_oot_v1",
        "mode": "semi_trusted",
        "provider_profile": "deterministic",
        "user_decisions": [],
        "faults": [],
        "expected_terminal_state": "succeeded",
    },
    trial_id="trial_001",
    artifact_root=Path("eval-results"),
)
```

`provider_profile` 支持三种明确语义：`deterministic` 不调用 LLM，`fake_provider` 调用无网络的固定测试替身，`configured_provider` 通过独立的 `provider` 参数调用真实端点。真实端点的 `api_key` 只在本次 Python 调用内存中传递，不写入评测工作区配置、Manifest 或 Trace；默认 CLI 和 API 不会主动调用真实 Provider，真实 Provider 多 Trial 仍需使用方明确配置并承担网络/密钥治理。

Adapter 会：

1. 拒绝覆盖同一 `case_id/trial_id`；
2. 创建隔离应用目录；
3. 安装固定种子多表合成 Fixture；
4. 运行同一 LangGraph、Tool Registry、Reviewer 和 Provider Gateway；
5. 在半信任模式自动提交预设决定，未指定时默认批准；
6. 等待终态并检查预期；
7. 导出 `result.json`、`trace-bundle.json` 和 `artifact-manifest.json`；
8. 按配置删除临时工作区，只保留脱敏评测结果。

它不直接读取内部 SQLite 表作为外部协议；数据库访问只封装在 Adapter 内部。

## 7. 当前故障注入

已支持：

- Provider 超时、401、403、429、空回复、非法 JSON；
- Reviewer 阻断与要求修改，两者在完全信任模式下都不能被自动批准；
- 指定 Worker 工具错误或超时；
- Human Gate 批准、带修改批准或拒绝；
- 固定 Fixture、随机种子、Case 和 Trial。

文件权限、磁盘不足、固定时钟、Checkpoint 损坏和进程级部分成功注入尚未形成稳定公共协议，保留在后续里程碑，不能写成已经交付。

## 8. Trace Bundle 数据边界

接口：

```text
GET /api/v1/runs/{run_id}/trace-bundle
```

Bundle 只包含：

- Run/Trace/Span 状态和哈希；
- 结构化错误与安全状态；
- Reviewer 状态和问题代码；
- Provider 生命周期与聚合 usage；
- 决策状态；
- 产物类型和 checksum；
- Run Manifest。

不包含完整本地路径、原始表、PII、密钥、客户级预测、模型文件、未脱敏 Prompt 输入或隐藏思维链。未来评测平台只能通过该脱敏边界或用户明确导出的评测包接入。

## 9. 当前自动化证据

仓库测试覆盖：

- Manifest 相同/不可比判断；
- 半信任完整案例、自动确认和安全 Trace 导出；
- Provider 超时与 Worker 故障组合注入；
- Reviewer 来源状态；
- JSON、Excel 和单页 HTML 报告中的确定性审核覆盖率、LLM 实际覆盖率与降级比例；
- Trace 父子关系、终态、API 和归档恢复；
- 固定黄金链路、报告、模型包和重载评分一致性。

这些仍属于本地合成和框架验证。真实 API 的多 Trial 成功率、方差、成本、Reviewer 召回/误报和人工 Judge 一致性必须由后续独立 Harness 建立 Baseline。

## 10. 本地 Harness 使用方式

Suite 通过以下接口保存在当前工作文件夹的 `evaluations/` 下：

```text
POST /api/v1/evaluations/suites
GET  /api/v1/evaluations/suites
POST /api/v1/evaluations/runs
GET  /api/v1/evaluations/runs/{run_id}
```

本地命令默认执行内置合成 Smoke Suite：

```text
python scripts/run_harness.py
```

每个 Run 会原子写入 `run.json`、每个 Case/Trial 的结果和脱敏 Trace Bundle。门禁至少检查预期终态、错误率、安全事件率和 Trace 完整性；Baseline/Candidate 比较前先检查 Manifest 可比性，条件不同会阻断直接归因。

## 11. 后续独立 Harness 平台

后续再建设真正的 Core/Edge/Safety/Recovery/Bad Case 数据集治理、受限 Holdout、PR/Nightly/Release Gate、人工 Rubric 与 LLM Judge 校准、线上反馈和权限治理。当前本地 Harness 是可替换的基础，不把它宣传成企业评测平台，也不得成为绕过 SafeEvidence 的新数据出口。
