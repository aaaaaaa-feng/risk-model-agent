# 10｜研发路线图与 Backlog

## 1. V1 已实现基线

- React/TypeScript/Vite 四区专业建模台与集中设置中心。
- FastAPI `/api/v1`、SQLite 领域模型、LangGraph checkpoint/HITL。
- 多表合成演示、CSV/Excel/数据字典、四级关联和 Notebook。
- 多 Y 独立任务、诊断、清洗、切分、筛选、分箱和候选模型。
- 独立 Reviewer、三轮修复/安全降级、SSE 和项目对话。
- 统一报告、模型包、批量评分、归档、备份、迁移和旧版只读保留。
- DeepSeek、Kimi、Kimi Code、OpenAI、Anthropic/custom Provider 设置与连通性测试。
- Run Manifest、层级 Trace Span、Reviewer 来源状态、脱敏 Trace Bundle、隔离 Target Adapter 与基础故障注入。

## 2. V1 发布门禁

1. 固定黄金数据和全量测试通过。
2. React typecheck/build、Python lint/compile、API 与报告一致性通过。
3. 1440×900、1280×800 和键盘流程实测。
4. 旧 runtime 完成快照、复制、数量/哈希核对后从仓库删除。
5. macOS 本机包启动冒烟；Windows CI runner 包启动冒烟。
6. Git 工作区只保留源码、锁文件、正式规格和必要脚本。
7. 推送后核对远端 SHA、默认分支、CI 和候选 artifact。

## 3. V1.1 质量增强

- 提供模型分箱单调合并建议与业务可接受例外记录。
- 完善高维类别变量与更多缺失码策略。
- 增加受控超参数搜索预算和可视化比较。
- 让报告图表具备打印/无障碍替代文本。
- 增加归档恢复和模型包跨版本兼容矩阵。
- 增加离线安装依赖包和受控更新通道设计。

## 4. V2 候选

- 独立评测 Harness 平台，读取脱敏 Trace Bundle。
- Core/Edge/Safety/Recovery/Bad Case 数据集、Holdout、真实 Provider 多 Trial、Evaluator 与 Baseline/Candidate 发布门禁。
- 内网服务器多人部署、SSO、RBAC、项目权限和资源队列。
- 审批流、模型注册、上线接口、监控和回溯治理。
- 机构自定义 Prompt/规则版本、Provider 路由和成本策略。
- 经过安全评审的 MCP 适配器；仍必须映射到强类型 Registry。

## 5. 明确后置

多分类/回归/时序、任意 Agent 工具发现、云端原始数据处理、远程任意代码执行、未隔离 Notebook、自动替代业务审批均不进入当前 V1。

## 6. 研发纪律

- 先读正式规格和现状，再计划、修改、测试、检查 diff、提交。
- 不把合成演示、框架测试、CI 候选包或供应商文档当成生产验证。
- 数据迁移先备份再清理，任何不兼容 Run 只读保留。
- 安全和数据合同失败时 fail closed；模型候选局部失败时隔离并继续。
- 每个版本保持唯一实现，能力迁移后删除旧单体和过期 API。
