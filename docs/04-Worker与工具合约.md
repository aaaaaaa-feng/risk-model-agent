# 04｜Worker 与工具合约

- 文档版本：v1.0
- 日期：2026-08-20
- 目标：定义本地确定性计算、工具注册、多维分析、安全和执行约束

## 1. Worker 的定义

Worker 是本地工具，不是 Agent。它由确定性 Python 代码实现，也不调用 LLM 作统计或模型计算；使用 pandas、Polars/DuckDB、scikit-learn、XGBoost 等受控库完成明确任务。

```text
Agent：决定为什么分析、分析什么、下一步是什么
Worker：按结构化参数准确计算并返回产物
```

同一数据版本、同一方案、同一工具版本和同一随机种子应得到一致结果；无法完全确定的底层算法必须记录来源和波动范围。

## 2. 工具化方式

Worker 能力通过本地 Tool Registry 暴露给 Orchestrator。执行模型按风险分层，不用“进程内优先”作为笼统默认：

- 只读、低风险、可证明有界的纯统计工具可以进程内强类型调用；
- 目标规模的大数据量画像、训练、报告导出和任何生成代码相关任务一律进入受控子进程；当前 V0.1 对经过导入前资源预估、且在默认边界内的画像/EDA 仍可采用进程内强类型调用，训练已使用受控子进程；
- 受控子进程由父进程施加超时、取消、资源、网络和目录限制，并通过本地 JSON-RPC 或等价协议通信；
- V1 不要求 MCP。未来如接入 MCP，只能作为经过审批的外层适配器，不能改变上述权限边界。

- Tool Schema 负责参数校验和权限声明；
- Worker 只接收本地 `dataset_ref`，不接收前端上传的任意路径；
- 结果保存为不可变 Artifact，工具返回摘要与引用；
- Agent 只能看到符合安全策略的结果摘要；
- Tool Registry 必须记录 `execution_class`（`in_process` 或 `sandboxed_process`）和允许的资源策略。

## 3. 通用调用合约

```json
{
  "schema_version": "risk-tool.call/v1",
  "tool_call_id": "tool_xxx",
  "project_id": "proj_xxx",
  "run_id": "run_xxx",
  "node_id": "screening",
  "state_version": 12,
  "tool_name": "segment_analysis",
  "tool_version": "1.0.0",
  "dataset_ref": "dataset://clean/v3",
  "plan_hash": "sha256:...",
  "arguments": {},
  "limits": {
    "timeout_seconds": 300,
    "memory_mb": 2048,
    "max_output_rows": 1000
  }
}
```

工具执行前必须校验：

- 项目、数据和方案引用存在且匹配；
- 调用节点允许该工具；
- 参数符合 Schema；
- 资源预测没有越过限制；
- 输出不会违反安全证据策略。

## 4. 通用结果合约

```json
{
  "schema_version": "risk-tool.result/v1",
  "tool_call_id": "tool_xxx",
  "status": "succeeded",
  "artifact_refs": ["artifact://segment-analysis/v1"],
  "summary": {
    "groups": 84,
    "suppressed_groups": 7
  },
  "warnings": [],
  "metrics": {
    "duration_ms": 1820,
    "peak_memory_mb": 310
  },
  "output_hash": "sha256:..."
}
```

失败结果必须包含稳定错误码、可重试性、用户可读说明和诊断引用。不得用空结果冒充成功。

## 5. V1 工具目录

| 工具 | 作用 | 关键输入 | 关键输出 |
|---|---|---|---|
| `inspect_dataset` | 校验文件与表结构 | 文件引用、Sheet、编码 | 数据版本、哈希、行列数 |
| `profile_dataset` | 基础画像 | 数据版本、抽样/分块策略 | 类型、缺失、唯一值、分布 |
| `analyze_target` | Y 与样本分析 | Y、正负标签、时间字段 | 0/1 分布、阻断与提示 |
| `segment_analysis` | 1—4 维聚合分析 | 维度、指标、过滤、分箱 | 聚合表、图表规格、警告 |
| `detect_data_issues` | 数据质量与泄漏规则 | 字段画像、角色、时间 | 问题列表与证据 |
| `simulate_cleaning` | 预估清洗影响 | 清洗计划 | 前后样本/分布差异 |
| `apply_cleaning_plan` | 执行已批准清洗 | 冻结清洗计划 | 新数据版本与审计 |
| `calculate_iv_woe` | 训练集 IV/WOE | 训练分区、分箱配置 | 分箱、WOE、IV、单调性 |
| `check_stability` | 稳定性分析 | 时间/样本分区 | PSI、分布变化和提示 |
| `analyze_association` | 相关性与冗余分析 | 候选变量、方法 | 相关簇、保留建议证据 |
| `select_features` | 应用筛选策略 | 策略版本、统计产物 | 入选/排除/原因 |
| `train_candidate` | 训练一个候选模型 | 冻结计划、算法配置 | 模型、验证/OOT 结果 |
| `compare_models` | 公平比较候选模型 | 候选结果、指标规则 | 排名、冠军建议和风险 |
| `generate_scorecard` | 生成逻辑回归评分卡 | WOE/LR 模型、PDO 等 | 分数映射和校验 |
| `explain_model` | 树模型解释 | 模型、允许样本 | 重要性/解释产物 |
| `render_report` | 生成专业报告 | 所有已验证产物 | HTML/XLSX/JSON 报告 |

工具目录是白名单。Agent 不能自行创建和调用未知工具。

## 6. 多维画像分析

### 6.1 AnalysisSpec

自然语言请求必须先转换为结构化规格：

```json
{
  "schema_version": "risk-analysis.spec/v1",
  "dataset_ref": "dataset://clean/v3",
  "scope": "train",
  "dimensions": [
    {"column": "age", "transform": "quantile_bins", "bins": 10},
    {"column": "channel", "transform": "category"},
    {"column": "application_month", "transform": "month"}
  ],
  "metrics": ["row_count", "bad_count", "bad_rate", "missing_rate"],
  "filters": [],
  "target": {"column": "bad_flag", "positive_value": 1},
  "min_group_size": 50,
  "top_k_per_dimension": 20,
  "max_groups": 1000
}
```

### 6.2 支持范围

- 单维：年龄分箱坏样本率；
- 双维：年龄 × 渠道；
- 三维：年龄 × 渠道 × 月份；
- 四维：年龄 × 渠道 × 地区 × 月份。

默认不建议一次性把四维所有组合铺平。界面优先提供逐级钻取、热力图、Top-K 和筛选器。

### 6.3 防止组合爆炸

执行前计算预计组合数与内存：

- 超过 `max_groups` 时先合并稀有类别或要求缩小范围；
- 低于 `min_group_size` 的小样本单元格隐藏或合并；
- 高基数维度默认仅保留 Top-K，其余归为“其他”；
- 连续变量必须先分箱；
- 输出行数和图表点数有硬上限；
- 计算结果必须标注被隐藏、合并和截断的组数。

## 7. 清洗工具边界

### 可按策略自动执行

- 空白字符和一致缺失标识；
- 明确的类型规范化；
- 训练集内拟合的常规缺失填补；
- 方案已明确的缺失单独成箱。

### 需要证据和确认或策略授权

- 异常值截断；
- 稀有类别合并；
- 大比例样本删除；
- 实体去重；
- 时间窗口过滤。

### 不允许 Worker 自行决定

- 改写 Y 含义；
- 把未知标签强制转成 0/1；
- 纳入被阻断的贷后字段；
- 改变训练、验证、OOT 角色；
- 用留出集决定清洗或筛选参数。

## 8. 变量筛选执行顺序

为支持 2 万字段和 8GB 内存，Worker 采用分阶段策略：

1. 元数据粗筛：常量、缺失、高基数、ID、重复、类型；
2. 泄漏与可用时间筛查；
3. 按列分块计算单变量统计、IV/WOE；
4. 稳定性与时间差异；
5. 在缩小后的候选集上计算相关性或共线性；
6. 生成带原因的最终变量集；
7. 冻结后才允许生成建模代码。

筛选阈值必须属于版本化策略，不能散落在 Prompt 或代码常量中。

## 9. 训练工具要求

- 数据切分由上游冻结，训练工具不能重新随机切分；
- 预处理只在训练分区拟合；
- 候选模型使用相同数据版本和验证协议；
- 留出/OOT 只在模型选择规则锁定后使用；
- 保存随机种子、依赖版本、线程数和参数；
- XGBoost 限制线程、树深、轮次和内存，支持 early stopping；
- 允许算法级类别不平衡处理（如 `class_weight`、`scale_pos_weight`），只在训练分区拟合并写入方案；当前 V0.1 不做重采样。Logistic/Random Forest/HistGradientBoosting 使用类别权重，XGBoost 的 `scale_pos_weight` 由训练分区正负样本数计算；结果必须写入 `imbalance_policy`（含 `fit_scope=train`、训练正负计数、策略和 `resampling=none`）。验证集与 OOT 不得参与权重拟合；报告应明确这不是业务阈值或生产策略结论；
- 单个候选失败不应让成功候选消失，但报告必须明确失败原因；
- 只有验证完成的产物可以进入正式报告。

## 10. 代码生成与 Worker 的关系

AI 生成代码用于可读、可导出和复现。V1 将生成代码定位为交付物，不在产品内执行生成代码；正式训练只由受控 Worker 根据 `ModelPlan` 执行，以降低任意代码风险。

如后续确实允许执行生成代码，必须经过：

1. AST 和依赖白名单；
2. 禁止网络、Shell、动态执行和越界路径；
3. Reviewer 审核；
4. 合成数据测试；
5. 受限进程执行；
6. 结果与 Worker 参考实现的一致性检查。

## 11. 资源和并发

- 默认串行执行内存密集型训练；
- 分析任务可受控并行，但总内存和线程数有全局上限；
- 每个工具支持超时、取消和进度事件；
- 数据导入时估算 DataFrame 放大倍数和特征矩阵规模；
- 资源不足时优先降级为分块、钻取或缩小候选，不应触发系统崩溃；
- 临时文件放在项目受控临时目录，完成或取消后清理。

## 12. 错误码示例

| 错误码 | 含义 | 可重试 |
|---|---|---:|
| `DATASET_REF_MISMATCH` | 数据引用与当前状态不一致 | 否 |
| `INVALID_ANALYSIS_SPEC` | 多维分析参数无效 | 修改后可 |
| `GROUP_LIMIT_EXCEEDED` | 分组组合超限 | 缩小后可 |
| `MEMORY_BUDGET_EXCEEDED` | 预计内存超限 | 降级后可 |
| `TARGET_CONTRACT_FAILED` | Y 不满足 0/1 契约 | 否 |
| `TRAIN_ONLY_VIOLATION` | 筛选或预处理偷看留出数据 | 否，需修复 |
| `TOOL_CANCELLED` | 用户取消 | 可新建尝试 |
| `ARTIFACT_WRITE_FAILED` | 产物保存失败 | 视原因 |

## 13. 普通软件测试

虽然独立评测 Harness 后置，Worker 研发必须立即具备：

- 单元测试；
- 输入输出 Schema 测试；
- 边界值与错误测试；
- 确定性/随机种子测试；
- 小型合成数据集成测试；
- 数据泄漏回归测试；
- 指标和报告一致性测试；
- 取消、超时和资源限制测试。
