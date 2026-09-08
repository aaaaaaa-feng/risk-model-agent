# 自主优化候选版验收记录

日期：2026-09-09。基线 `86c8014`；主要实现 `1f0dc1c`、`bf3a5ac`、`2814dde`、`ba21194`；预检收尾 `4e9bc54`。这些记录不代表真实信贷数据效果、真实 LLM 稳定性或真人使用验收。

## 前后证据

基线 [记录](2026-09-08-baseline.md) 已保存：19 个正常／人工拒绝／工作进程相关检查通过，另 1 个 Fake Reviewer 阻断通过。原实现重复审核不改方案，且每轮计算 OOT。

新版实际训练执行不同计划，独立轮次包防覆盖；最终选择最佳后才评估 OOT；聊天确认产生真实新运行；重复提交不会再创建运行。全仓审查及修复详见 [审查报告](../audits/2026-09-09-full-code-review.md)。

## R01—R14 对照

| 题目 | 已实现与证据 | 验证层级／边界 |
|---|---|---|
| R01 初始达标 | 目标满足即停止；`test_initial_target_met_stops_after_one_real_round` 实际AUC目标0.5，一轮完成且拟合计数对应唯一轮次 | 真实 Worker，LLM 关闭；主目标固定 AUC |
| R02 预设可改善 | `test_constructed_nonlinearity_has_real_improving_legal_change` 构造非线性夹具，合法 Extra Trees 变更较 Logistic 的开发 AUC 提高 >0.1 | 实际统计模型、教学构造夹具，测试直接给定合法特征，不能代替端到端真实 LLM 改善 |
| R03 后轮更差 | `test_second_worse_round_does_not_overwrite_best` 检查较差、微增和显著改善；真实 Worker 校验全局最佳引用 | 构造指标证明比较规则；实际多轮证明文件／报告同候选 |
| R04 无法达到目标 | 三策略合成试验目标 1.0，优化在第三轮连续无改善停止，保留第一轮；goal_status=unmet | 实际 Worker，无虚构提高 |
| R05 重复方案 | `validate_patch` 及计划哈希阻断无变更/已尝试方案；`test_patch_rejects_oot_leakage_code_and_unchanged_plan` | 规则边界单元测试；真实试验三轮计划均不同 |
| R06 泄漏／OOT | PatchPlan 严格字段、变量白名单和证据引用；`test_modeling_v11.py` 访问观测；最终检验一次 | 开发阶段不对 OOT 预测；标签/划分/评价口径不可变 |
| R07 人工／Reviewer | `test_optimization_human_refusal_never_runs_next_round`，原 Fake Reviewer block/revise 用例；阻断不可被重复审核降级 | 人工拒绝后仅初始轮；Fake 必须标识 |
| R08 异常／取消 | 真实产品写入子进程硬超时后停止写入；原评测真实进程 timeout/cancel；429 一次独立重试；用户取消终态保护 | OS 进程检查需正常权限；结果不明超时不自动重放 |
| R09 重启／重复提交 | 幂等 request_key、原 migration/checkpoint 回归、`test_recovery_blocks_uncertain_training_without_replaying` 证明不可确认副作用节点安全阻断且不提交执行 | 可从合法检查点恢复；不宣称任意故障自动恢复 |
| R10 聊天行动 | API 对话提议／批准／重复批准回归；页面实际聊天“请重新训练”后产生新 run_id 并跳工作台，最终完成 | 本地合成数据真实运行，不只是文本回执 |
| R11 预检 | 样本标签、聚合划分、算法/内存、配置与未测试连接、有限单次/耗尽月预算；4 个专项测试 | 连接未经真实测试显示 not_tested；预检建议需运行中确认 |
| R12 快照 | objective_snapshot 哈希固定目标、预算、数据、划分、评分、禁止项；重复键改目标拒绝；新 Run 产生新快照 | 目标不允许同运行暗改；审批前编辑进入最终版本 |
| R13 比较 | 同数据/工作数据/划分/目标/评分/预算检查；未知或不一致不可比；三策略同上限实际对照 | 单次合成实验，不统计显著性或贡献归因 |
| R14 自检 | 交付前重载真实导出包，32 样本结果差异 0；缺失/新类别验证；现有包版本/字段/类型/哈希加载边界测试 | 本地同依赖版本有效，不是线上审批、授信或跨版本迁移验收 |

## 三策略等上限实验

固定提交 `ba21194`，夹具 `synthetic_time_oot_v1` 生成 500 行，固定目标 FPD0 与同一划分。每策略拟合上限 300、工具计算上限 300 秒，最多 3 次优化，单进程顺序；LLM 关闭，外部模型调用 0。上限一致不意味着实际消耗相等。详细聚合证据、计划、哈希及交付检查：[JSON](2026-09-09-synthetic-comparison.json)。

| 策略 | 轮数 | 实际拟合 | 工具完成事件 | 总耗时秒 | 最大事件观察间隔秒 | 开发 AUC | 最终 OOT AUC | 停止 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 固定流程 | 1 | 9 | 16 | 23.94 | 2.82 | 0.6926 | 0.8013 | 固定流程完成 |
| 参数搜索 | 1 | 14 | 16 | 22.10 | 2.40 | 0.6840 | 0.8182 | 参数搜索完成 |
| 本地诊断优化 | 3 | 27 | 22 | 34.05 | 3.87 | 0.6926 | 0.8013 | 连续无改善 |

三者均 succeeded、业务目标均 unmet、交付自检均 passed。优化轮次分别执行 Logistic、Extra Trees 深度6、深度9，最终保留 Logistic。参数搜索的最终 OOT 较高不能用于重新选择优化赢家；选择只按开发验证规则。该例没有证明优化收益，优化额外消耗18次拟合、约10秒。每种仅一次，执行时本机也在跑测试，耗时不能当正式性能基准。

## 自动检查实际结果

- 最终实现 `c1abbf5` 完整Python回归：**291 passed**，1项Starlette/httpx弃用提示，219.42秒。

- `ba21194` 计算链路完整 Python 回归：286 passed、1 项 Starlette/httpx 弃用提示，195.07 秒。
- `4e9bc54` 预检与 API 相关：10 passed，6.90 秒；包含新增月预算耗尽检查。
- 审计专项：16 passed，57.74 秒；包含真实写入子进程硬终止（权限可用环境）。
- R01初始达标真实Worker、R09副作用不明重启专项：2 passed，23.38秒。
- 评测Token预算契约与Provider边界：先10项通过，再补OpenAI/Anthropic未知usage检查及原安全用例16项通过；全部无真实网络调用。
- 前端：19 文件、90 项通过；白天/黑夜消息对比度通过；typecheck、lint、format:check、build 通过。
- Ruff 检查及131文件格式检查通过；打包与桌面契约静态检查 valid=true。
- 远程6c38e43的 [CI34252340892](https://github.com/aaaaaaa-feng/risk-model-agent/actions/runs/34252340892) 五项全通过：前端、Python3.11/3.12/3.13、Windows桌面壳；最终文档提交状态见根目录handover.md的分支CI索引。不同层级测试有重叠，不能相加为独立案例总数。

## 三段演示与重现

1. **正常交付与不伪造收益**：运行 `python scripts/run_optimization_demo.py --output <不存在的隔离目录>`；输出 summary.json 与本地独立 workspace。展示固定方案、真实参数变更、最佳包、最终 OOT 和交付自检。脚本包含 `if __name__ == '__main__'`，用于 spawn 子进程。只生成合成数据。
2. **失败与恢复边界**：运行 `pytest tests/test_audit_optimization_boundaries.py::test_product_worker_deadline_stops_real_writes tests/test_migration.py tests/test_api_agent.py`。先证明工具超时停止写入，再展示合法 checkpoint 恢复；不确定副作用必须阻断，新建运行有新ID，不盲目重训。界面上保留失败／阻断证据。
3. **正确停止与聊天重训**：运行 `pytest tests/test_optimization.py::test_optimization_human_refusal_never_runs_next_round tests/test_conversation_responses.py`。在本地工作台打开合成项目，输入“请重新训练”，先显示待确认提议，确认后进入真实新运行；目标1.0示例会如实连续无改善停止。完全信任只自动批准既定节点，Reviewer 阻断仍有效。

页面实际验收使用隔离工作区、loopback 8876；首轮 run_d7228079e5b0d011，聊天确认后 run_82f695f367fc674f，均3轮27次拟合，AUC0.6926无改善。应用状态、参数和回执通过浏览器读取核对。此页面验收固定于工作台里程碑，末次预检展示修复由相关回归、类型与生产构建验证。

## 外部条件

真实 Provider 配置存在，但未得到有限预算答复，未发起真实调用；后续需要明确有限 Token 总额度、单Run分配和重复次数，才能测真实计划质量、延迟与波动。API Key 不进入本文件或 Git。真实数据、真人用户、安装包实机均未验证。AgentEval 的独立报告以其固定版本和执行器标签为准，不将跨版本规则证据完整性改善写成模型效果提升。
