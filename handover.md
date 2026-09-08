# 风控建模 Agent 研发交接

更新日期：2026-09-09。本次在项目原 checkout 的隔离分支 `codex/risk-autonomous-optimization` 实施，初始工作区干净；未修改其他项目源码。本文是根目录最终交接入口，协作过程仅索引到 [STATUS](docs/coordination/STATUS.md) 和 [HANDOFF](docs/coordination/HANDOFF.md)。

## 目标、完成范围与当前定位

本次目标是把单次建模与重复审核改成可解释、有限、可审批的真实优化闭环，并完成任务书 P0/P1、R01—R14。已经实现诊断→白名单方案变更→实际重训→固定协议比较→保留历史最佳→有限停止；运行前诊断、目标快照、聊天行动、历史比较和模型包交付自检均接入现有工作台。

当前定位为**可以本地演示、具有真实执行闭环的求职项目候选版**。已验证的是代码边界、合成数据上的真实统计建模、实际子进程和工作台操作。真实 LLM 诊断质量及多次稳定性没有验证，不能称为“真实模型自主优化效果已验收”或生产系统。

完整逐题映射、分母、前后证据和三段演示在 [验收记录](docs/evidence/2026-09-09-acceptance.md)，机器可读聚合结果在 [三策略证据](docs/evidence/2026-09-09-synthetic-comparison.json)，全仓复查见 [审查报告](docs/audits/2026-09-09-full-code-review.md) 与 [文件清单](docs/audits/2026-09-09-file-inventory.md)。

## 具体修改与原因

| 文件／入口 | 修改及原因 |
|---|---|
| `app/domain/optimization.py` | Objective、PatchPlan、参数／模型／变量白名单、计划哈希、可行性优先的比较及明确标识的本地诊断策略；防止越界和无变更重跑 |
| `app/services/pipeline.py` | 固定目标/数据/划分/评分快照；保存完整轮次证据和独立 bundle；按严格最佳保存模型；耐心阈值只影响继续优化；Provider 预留预算；只在最终冻结后做 OOT |
| `app/workers/modeling.py` | `evaluate_oot=False` 开发期训练、候选实际参数与拟合计数；独立 `evaluate_final_holdout`；消除优化反馈中的 OOT 和全表预测 |
| `app/orchestration/graph.py`、`contracts.py` | 图 v3 新增诊断/确认/应用/冻结；幂等创建、取消终态保护、恢复副作用不明时阻断；累计工具计算预算 |
| `app/orchestration/process_runner.py` | 真实 Worker 硬超时、终止后代进程、确认清理；临时密钥通过私有子进程参数传递，不写公共状态 |
| `app/services/run_readiness.py`、`app/api/runs.py` | 聚合样本/建议划分/算法/预算/连接状态预检；新建执行前阻断；同原始及工作数据、划分、目标、评分与预算才可直接比较 |
| `app/services/conversations.py`、`app/api/conversations.py` | 聊天要求重训形成待确认提议，批准后幂等创建真实 Run；回执关联运行；解释性或否定请求不触发重训 |
| `app/services/artifacts.py`、`app/workers/reporting.py` | 导出后重载同样本自检、字段与异常输入策略核验；报告包含最佳、轮次与停止原因；缺最佳文件失败，不暗中重训 |
| `app/providers/gateway.py` | 输入+输出保守预留，429 最多一次独立重试；结果不明超时不自动重复请求 |
| `app/evaluation/adapter.py`、`contracts.py` | evaluation 与 product_worker 两轨，真实产物拷贝/哈希，明确模式和未知成本；configured_provider 必须有限 Token 预算 |
| `frontend/src/features/data/ui/RunPreflight.tsx` | 运行前目标/轮数/拟合/时间/策略设置，样本和建议划分、资源、阻断与未测试连接说明 |
| `frontend/src/features/runs/ui/OptimizationProgress.tsx`、`HistoryView.tsx`、`DecisionWorkbench.tsx` | 真实轮次与计划变化、最佳/停止/取消/交付检查，两个运行差异及可比性，优化审批 |
| `frontend/src/features/chat/` | 提议、批准/拒绝、执行回执与真实运行跳转；具体入口按目录组件复用 |
| `scripts/run_optimization_demo.py`、`tests/` | 可重现三策略合成真实 Worker 演示与 R01—R14 对应回归；不安装第二套平台 |

## 关键设计取舍

1. 默认初始1轮+最多3次优化，最多4轮；内部候选搜索另算拟合预算。主目标仅支持开发 ROC AUC，默认0.75是产品示例；KS、训练开发差距、PSI和变量数作为可行性约束先判断，不声称同时优化所有指标。
2. Test 是开发选择集。禁止修改标签、划分、OOT、评价标准和任意代码；仅允许已筛选合法变量子集及白名单模型参数，实际预处理重新在 Train 拟合。最终 OOT 在最佳冻结后评估一次；若今后据此开发，必须另设未接触过的最终数据。
3. 每次严格更优都保存最佳，即使增量小于0.001；是否继续优化独立使用 min_improvement/patience。较差候选不能覆盖历史包。
4. 半信任模式暂停审批，拒绝不继续；完全信任沿用授权自动批准，不能越过 Reviewer block/revise。删除旧的同方案重复训练/审核降级逻辑；未修复的问题保持失败或阻断。
5. 目标、数据与 Provider 配置快照被冻结，修改目标需要新运行；聊天重训继承已确认目标而不是过期请求。request_key 重复请求返回原 Run，目标冲突拒绝。
6. 每个工具在真实子进程执行，有累计工具时间和单工具硬超时。人工等待不计累计计算秒数；另有从建模方案冻结起算的优化墙钟期限，长时间审批后继续可能超时。无法确认副作用的重启不自动重放，应检查证据后显式新建 Run。
7. 无LLM时确定性策略可完成真实训练，但必须标明 deterministic_product。配置轨尝试结构化模型计划，非法、无响应或重复计划会停止，不靠本地策略暗中补成“模型成功”。Reviewer原有降级信息与Provider请求证据保留。
8. 预算用候选拟合、时间与Token上限控制；Token保守预留可能提前阻断，优先避免未知费用。货币成本未知为null；失败候选拟合计数可能是已完成可观测值，预算执行按更保守的预留拟合数，不按显示轮数估算总资源。

## 跨项目接口与版本

- 内部：`risk-agent-eval-result/v1`，`risk-agent-target-adapter/v1`；图版本由 `app/governance/manifest.py` 中 `AGENT_GRAPH_VERSION` 固定。
- 补丁 `risk-patch-plan/v1`，目标 `risk-objective/v1`，目标快照 `risk-objective-snapshot/v1`，预检 `risk-preflight/v1`，比较 `risk-comparison/v1`，交付自检 `risk-model-delivery/v1`，聊天 `risk-chat-action/v1`。
- Python入口：`app.evaluation.adapter.run_eval_case(case=..., trial_id=..., artifact_root=..., provider=None, cancel_event=None)`，关键字参数。
- `EvalCase`：fixture仅`synthetic_time_oot_v1`；target仅FPD0/FPD7/MOB30；executor_track为evaluation（直接领域执行，允许明确Fake故障）或product_worker（实际产品Worker，禁止Fake注入）；provider_profile为deterministic/fake_provider/configured_provider；objective沿用版本化约束；timeout_seconds和cleanup_workspace显式。
- 真实配置轨provider对象必须包含 `base_url`、`model`、`api_key` 和正整数 `run_token_budget`，可选项见适配器。密钥不写入公共state或输出；不要把provider对象打印到日志。完成后exports/artifacts含可离线核验字节、relative_path与sha256。
- Token预算能力：`app.evaluation.adapter.evaluation_budget_capabilities()` 返回 `risk-eval-budget/v1`，scope为run_planner_and_reviewer。每次输入UTF8字节+输出上限+256预留，所有Planner/Reviewer共用Run账本，429重试另预留；usage.total_tokens未知为null，budget_tokens_used按实际/预留最大值累计。外部调度器必须按trial预留Token总额，不能换算为未知美元。真实Profile拒绝0、负数、非整数（包括布尔值）预算；能力声明需与固定源码和回归一起核验。
- 产品HTTP均在 `/api/v1` 下：`POST /runs/preflight`、`POST /runs`、`GET /runs/compare?left=...&right=...`、`POST /runs/{id}/cancel`，聊天 `POST /projects/{id}/conversation/actions/{action_id}`；请求与返回以对应Pydantic/API实现为准。
- AgentEval外部合同 `agent-eval/1.0.0`（联调期）；对方当前实现 `50c14b61947d1c581dd56963b707e99c8e9df89c`、当前评分器 `domain-graders/3`。旧新版独立报告中的历史重评分实际版本为 `domain-graders/2`，不可改写为3。
- 对方报告位于 AgentEval仓库 `reports/dual-domain/2026-09-09-risk.md`，索引 `risk-evidence-summary.json`，失败草稿 `risk-failure-drafts.jsonl`。已核实读取，报告尚待对方最终提交/推送；对方已回报4e9bc54生产Worker单题实际通过（47.61秒），仍非真实LLM；本项目不修改或代发其源码。
- 已完成联合证据覆盖旧86c8014/新bf3a5ac，各risk-dev/3五题×1；旧2/5规则通过、3/5缺证据，新5/5规则通过，属于历史证据重评分，不能换算质量提升。MOB30产品Worker0/3优化各1次，开发选择值0.7099→0.7157、26.69→45.08秒，目标均unmet；并非真实LLM收益。最终审计提交ba21194/预检4e9bc54已同步，对方最终复跑如未回报则记未测。

## 启动、停止与测试

依赖 Python3.11—3.13、Node22.12+；使用现有虚拟环境或按 README 安装。所有命令在本仓库执行。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cd frontend
npm ci
npm run build
cd ..
RISK_AGENT_OPEN_BROWSER=0 RISK_AGENT_PORT=8765 .venv/bin/python run_local.py
```

打开 `http://127.0.0.1:8765`，通过设置配置模型与有限预算，或明确关闭LLM演示本地策略。本次临时8876演示服务已优雅停止并核对端口释放，隔离合成数据保留。服务仅loopback；命令终端Ctrl+C优雅停止，确认对应端口已释放；不删除用户数据目录。专用数据目录使用 `RISK_AGENT_DATA_DIR`，隔离演示可加 `RISK_AGENT_AUTO_MIGRATE=0`，避免自动搬入历史数据。

```bash
.venv/bin/ruff check app tests scripts run_local.py
.venv/bin/ruff format --check app tests scripts run_local.py
.venv/bin/python -m pytest
.venv/bin/python scripts/verify_packaging.py
.venv/bin/python scripts/run_optimization_demo.py --output /tmp/risk-demo-unique
cd frontend
npm run typecheck
npm run lint
npm test
npm run format:check
npm run build
```

`--output`必须不存在，脚本自动关闭执行器。产物在独立目录，可通过summary.json检查模式、计划、预算和真实包自检；不要上传整个workspace。测试使用临时合成数据；进程树检查在受限沙箱可能被操作系统拒绝，需要正常本机进程权限，不能修改断言冒充通过。

配置名称：`llm_enabled`、`provider`、`api_format`、`base_url`、`model`、`reviewer_model`、`run_token_budget`、`monthly_token_budget`、`memory_budget_mb`、`max_parallel_models`、`default_models`；密钥可通过 `RISK_AGENT_API_KEY` 或应用密钥存储提供。不要提交 .env/secret配置/业务CSV/缓存/模型包或私人资料。Objective可设置target_value、max_optimizations、max_candidate_fits、max_seconds、patience等；完整域以 `Objective` 为准。

## 实际验证与未测项

- 新增R01初始达标真实单轮和R09副作用不明重启阻断：2项通过（23.38秒）；Planner/Reviewer跨网关共享预算与OpenAI/Anthropic未知用量回归通过。
- 最终实现 `c1abbf5` 全量Python回归 **291 passed**、1项Starlette/httpx弃用提示，219.42秒；此前计算链路完整回归286项通过（195.07秒），预检/API收尾10项通过（6.90秒）；前端90项及对比度、类型、Lint、格式、生产构建通过。测试有重叠不能相加。
- 三策略同上限、各1次实际Worker合成实验（500行）：开发AUC固定0.6926、参数搜索0.6840、本地优化0.6926；拟合9/14/27次；总时长23.94/22.10/34.05秒；优化未提高指标，三者目标均未达。各包32样本重载差异0，缺失/新类别检查通过。结果固定ba21194。
- 浏览器实际确认：先显示提议，批准后新run_id与工作台联动，3轮结束保留原最佳；没有用动画冒充执行。
- 全仓文件清点271个、47,280行，入口/边界扫描和重点路径深查，9类问题修复并回归；不是对第三方依赖或所有行的形式化正确性保证。
- 当前真实Provider配置虽存在，有限调用预算尚未获答复，未发起真实调用。需要明确Token总额、每Run上限、重复次数；再做同题多次真实product_worker，记录合法计划、失败、延迟、Token与未知费用，不用Fake替代。
- 未验证真实业务数据、真实用户、专家盲审、真实LLM波动、跨平台安装器实机。Package工作流只在main或手动触发，本分支不发布安装器；CI桌面壳测试不等于完整安装升级验收。
- 已知兼容限制：旧图/无manifest的中断运行要求新建；不同工作数据或缺快照不可直接比较；长期人工等待可能触发优化墙钟期限；硬时间不足可能以failed终结而非成功交付；不会保证无信号数据提升。

## 可用于简历的事实与证据

**用户提出并负责的产品要求**：有限自主优化、人工确认与Reviewer阻断、最终OOT隔离、真实行动与工作台联动、预算约束、真实证据及三项目联调。不能从这些要求推断已经做过用户访谈、上线或商业验证。

**本次Agent实施与验证**：在已有风控工作台上完成版本化合法补丁、真实训练与最佳保留、运行预检和目标快照、模型交付自检、评测双执行器接入，并完成上述回归及合成对照。实现方案和代码由Agent协助，求职叙述应能解释自己的产品判断与验收口径。

可准确叙述：“将风控建模流程扩展为受预算和审批约束的诊断优化闭环，用固定协议、最佳保留和最终留出隔离防止无效重跑与选择偏差；通过真实Worker合成试验及独立评测核对动作、终态与产物。”具体数字只能引用本文件分母及证据范围。

不能叙述：已服务某数量用户、提高某业务转化/坏账效果、稳定提高建模AUC、节省某百分比人力、通过真实模型多次评测或已上线。没有来源的旧指标不沿用。

## 分支、提交与GitHub

仓库：[aaaaaaa-feng/risk-model-agent](https://github.com/aaaaaaa-feng/risk-model-agent)，核实origin完全一致，当前PUBLIC，保持可见性。未合并main、不强制推送、不上传私人资料。

- 基线：[86c8014](https://github.com/aaaaaaa-feng/risk-model-agent/commit/86c8014eb2db6789ea6ff3d9fd6f210648d911c3)
- `1f0dc1c`：开发期／最终留出隔离。
- `bf3a5ac`：真实有限优化、审批、最佳与交付。
- `2814dde`：聊天行动、预检及可比较工作台。
- `ba21194`：全仓审查、预算/子进程/临时密钥/微小改善最佳修复。
- `4e9bc54`：预检实际划分与月预算、可读资源展示。
- `c1abbf5`：Token预算能力协议、跨Planner/Reviewer账本与未知用量证明、R01/R09验收。
- 后续验收与交接提交见 [分支历史](https://github.com/aaaaaaa-feng/risk-model-agent/commits/codex/risk-autonomous-optimization)。

[交付分支](https://github.com/aaaaaaa-feng/risk-model-agent/tree/codex/risk-autonomous-optimization)；[本交接文档](https://github.com/aaaaaaa-feng/risk-model-agent/blob/codex/risk-autonomous-optimization/handover.md)；[本分支CI](https://github.com/aaaaaaa-feng/risk-model-agent/actions?query=branch%3Acodex%2Frisk-autonomous-optimization)。远程实现交付核对：`6c38e43eafaf2a1290dcdf78aca36f703b19756d` 本地与远程一致，根交接文档读取成功且Git blob哈希一致。该提交触发的 [CI 34252340892](https://github.com/aaaaaaa-feng/risk-model-agent/actions/runs/34252340892) 已完成且全部成功：前端、Python3.11/3.12/3.13、Windows desktop-shell，共5项。后续仅补充此验收记录；最终文档提交与实时CI状态见上方分支历史及本分支CI链接，不循环把文档自身提交SHA写入自身内容。

## 后续优先级与具体动作（计划，未完成）

1. **P0 真实模型验证条件**：确认有限Token总额与每次分配，在独立合成环境跑相同题至少3次product_worker；将Provider请求记录、合法补丁比例、停止原因、耗时/用量交给AgentEval。预算不足应保留阻断，不放宽门槛凑成功。
2. **P1 固定最终版本外部回归**：AgentEval使用当前分支固定提交重跑原五题和同预算对照，版本指纹变化不能复用旧结论；记录grader3与历史grader2差异，不混为同一次结果。
3. **P1 专业与设备验收**：安排人工盲审诊断理由/约束解释、受控数据验证及Windows安装器实机；没有授权数据与设备前只保留计划。
4. **P2 进一步优化**：根据真实失败案例扩展受限特征调整和诊断策略；若同协议对照无收益，保留固定方案入口。任何使用过OOT指导改进的下一版需新的最终留出集。
