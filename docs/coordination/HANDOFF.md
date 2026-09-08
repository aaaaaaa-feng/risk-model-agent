# 跨会话协作索引

2026-09-08：已通过任务工具核实总协调 01a0817a-56a4-7fa2-9e72-51cebfe177fa，向其报告本任务身份、目录、分支及实施范围。尚未确认新的面搭子／AgentEval 实施任务；不向历史任务猜测发送请求。接口提案及后续回执记录于本文件。最终交付另见根目录 handover.md（完成时创建）。

## 已确认身份与接口映射

- 总协调已确认 AgentEval 实施：01a081ad-781a-7c83-bd38-d225fb29a0c4；面搭子：01a081ac-bec0-7e21-b791-df381b53f01e。本任务已再次读取评测任务请求核实身份。
- 读取并接受外部合同 1.0.0-draft.1 的对象和边界，具体联合验收待评测执行。调用仍为 `app.evaluation.adapter.run_eval_case(case=..., trial_id=..., artifact_root=..., provider=None, cancel_event=None)`。
- `EvalCase.executor_track`：evaluation（原直接领域执行，允许明确 Fake 注入）或 product_worker（真实 WorkerProcessRunner，禁止 Fake 注入）。唯一夹具 synthetic_time_oot_v1；目标 FPD0/FPD7/MOB30。
- `EvalCase.objective` 可配置有限目标／预算。结果 usage 明确 executor_track、execution_mode、candidate_fits、goal_status、cost_usd（未知 null）。
- 原始 Trace 的 run.state 提供 objective_snapshot、optimization_rounds、best_round_id、optimization_stop_reason、goal_status；产品完成与业务目标达成不同。
- artifacts 清单增加 relative_path/sha256；exports/artifacts 保存真实模型/报告字节，删除隔离 workspace 后仍能复核。
- 已给评测授权的本次范围：零 LLM 费用，正常/拒批/worker_error 各一次、每次最多300秒、单进程顺序执行。基线固定 86c8014，核心新版固定 bf3a5ac；不得用工作区未提交状态宣称固定版本结果。
- 评测回报：基线正常 succeeded、拒批 blocked；一次故障注入使用错误工具名，已记录为测试配置问题，不计为产品缺陷，待修正重跑。
- 总协调补充：最终 handover.md 增加“可用于简历的事实与证据”，不沿用无来源的效率/完成率数字，不写私人简历。
