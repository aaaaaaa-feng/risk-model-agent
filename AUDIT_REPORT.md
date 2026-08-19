# 风控建模 Agent 代码审计报告

- 审计对象：当前仓库 `risk-model-agent`
- 审计范围：数据导入、SafeEvidence/Provider、LangGraph 状态流、Worker、模型训练、报告导出、前端交互、测试与 Git 卫生
- 审计日期：2026-08-20
- 结论：本地 MVP 的关键安全和结果边界已通过；不能把它描述成已经完成跨平台安装、真实供应商验收或生产模型治理的平台。

## 已修复的高风险问题

1. 变量筛选原先可能在整表上计算 IV。现在筛选先冻结切分，再通过 `fit_positions` 只使用训练行，并在结果中写入 `fit_scope=train` 与 `fit_rows`。
2. 半信任流程原先在清洗确认后可能把界面阶段回写成 `planning`。现在清洗确认保持 `cleaning` 阶段，确认后从冻结状态进入 `screening`。
3. 确认卡原先只记录确认动作。现在 Y、切分方式和候选模型会校验、写入版本化方案，并实际影响筛选/训练；不支持的模型或不合规 Y 会拒绝。
4. 报告协议原先写成未实现的 `train_cv`。现在明确为 `train_only_oof_diagnostic → validation_select → oot_once`，OOF 按训练折重新拟合预处理/WOE，冠军仍只由冻结验证集选择，指标协议版本为 `risk-validation/holdout-v1`。
5. 报告写入与 checksums 的顺序原先可能导致报告自身哈希过期。现在先固定产物清单，再写 JSON/HTML/XLSX，最后生成 checksums。
6. 多维画像原先可能先构造过大的笛卡尔积再截断。现在先做 Top-K/`<OTHER>`、分箱上限和组合数预估，超限返回 `GROUP_LIMIT_EXCEEDED`。
7. CSV 导入增加 UTF-8/GB18030 解码回退；XLSX 多 Sheet 必须明确选择，不再静默使用第一张表。
8. Provider 的单 Run/单月 token 预算不再只是界面字段：调用前检查额度，返回 usage 后写入本地 `provider_usage` 审计表，超限返回 `PROVIDER_BUDGET_EXCEEDED`。
9. Baseline、what-if 和清洗执行已进入本地 API/报告链路：Baseline 在同一冻结样本上输出固定通过率和 swap set 聚合比较；what-if 从已完成 Run fork 独立实验 Run；批准的去重/IQR 动作创建不可变新 DatasetVersion。
10. 导入前新增 CSV/XLSX 行列与保守内存估算，超过本地边界会在物化表格前阻断；同时增加脱敏 Trace JSON/ZIP 下载、数据字典本地语义联动、校准分箱、训练集相关性/PSI 和候选变量重要性。
11. Provider 出站请求新增本地脱敏摘要、策略版本和内容哈希接口；Trace 对原始列名做稳定别名化，并校验事件链。
12. 项目备份新增安全恢复入口：包含数据的包会重新映射本地 ID，默认不含数据的包只恢复元数据并明确列出缺失数据集，不覆盖已有项目。
13. 变量筛选结果和 Provider 出站摘要原先只有后端能力、页面不可查；现在报告提供字段搜索/状态筛选/排序和隔离 what-if 入口，行动面板提供脱敏出站请求审计列表。

## 已验证证据

- `32 passed`（当前本地环境）：单元、Worker、API、半信任恢复、XLSX Sheet、Provider DLP/预算/出站摘要、报告导出、Trace 脱敏与事件链、资源边界、数据字典、校准/稳定性/评分卡映射、训练集筛选、高维分析守卫、Tool Registry、清洗版本、清洗确认门禁、Baseline 和 what-if 隔离、项目多轮对话、报告叙事锁定、聊天文本边界、跨平台打包契约和黄金回归。
- `ruff check app tests scripts/*.py`：通过。
- `node --check app/static/app.js`：通过。
- `git diff --check`：通过。
- `python scripts/verify_packaging.py`：通过；这是入口和资源契约检查，不等价于目标平台真实打包。
- `.venv/bin/python scripts/run_golden_cases.py`：5/5 通过；这是最小确定性回归门禁，不等价于独立评测 Harness。
- 本地 Git 最新提交：
  - `3967feb docs: record final regression count`
  - `74e22b6 fix: keep free-form chat local by default`
  - `c776715 feat: add guarded chat and feature confirmation controls`
  - `c62e11e feat: expose report narrative editing in web UI`
  - `448db66 feat: add conversational agent and report narrative workflow`
- 外部 Provider 默认关闭；启用后只发送别名化 SafeEvidence 和受控结构化聊天意图，测试覆盖敏感载荷阻断、聊天文本边界、疑似敏感值发送前阻断和 OpenAI-compatible 请求形态。
- 生成代码只作为本地交付物，产品不执行任意生成代码；静态 Reviewer 阻断不会被标成成功。
- 代码 Reviewer 已覆盖 AST 语法、依赖白名单、危险调用、网络/凭据模式，并给出结构化位置；项目对话只向 Provider 发送别名化上下文，Trace 不导出聊天原文。
- 当前验证使用 FastAPI TestClient 和本地 Worker 子进程；本环境不把一次受限沙箱端口绑定失败当作应用功能失败，也未把它包装成真实浏览器/跨平台验收。

## 仍明确未完成、不能宣称的事项

- 尚未用真实供应商 API 做连通性、限流、费用和服务条款验收；`llm_enabled` 是显式开关。可选 `.[secure]` 依赖会优先使用 macOS Keychain/Windows Credential Manager 等系统后端，当前环境未做目标系统验收，失败时回退到 600 权限本地文件。
- Reviewer 已实现最多三轮“冻结方案 → 受控模板重生成 → 重审”，并保留 `generated_model_v1/v2/v3.py`；它不是任意自然语言代码的语义修复器，三轮仍未通过就阻断。
- 当前不做超参数搜索；训练分区在 10,000 行以内提供 3-fold OOF 诊断，冠军仍按冻结验证集选择，超过资源上限会显式跳过。报告新增校准分箱、训练集拟合的 PSI/相关性复核和模型变量重要性，但这些证据不自动替代人工/治理判断。
- 清洗目前自动执行的只有安全标准化；去重和 IQR 分位截断可在确认节点显式批准并产生新数据版本，稀有类别合并和样本删除仍未实现。
- 评分卡已是 WOE + Logistic 路线，包含训练集分箱、WOE/IV、PDO/Base Score/Odds、分箱分值、评分—概率映射校验和训练/验证/OOT 指标；单调性、模型包跨版本加载和目标规模实测仍属后续版本。
- 已提供 PyInstaller spec、macOS/Windows 启动和构建脚本，但尚未在两个目标系统真实构建、签名、安装、升级/卸载验收，也没有把普通 Web 启动方式冒充“拿来即用”的桌面发行版。
- 独立评测 Harness 按约定后置；当前仅保留 Trace 和普通软件回归测试。

## GitHub 尝试结果

按用户授权尝试执行公开仓库创建和推送，但当前 GitHub CLI 认证 token 已失效，且沙箱代理连接被拒绝：

```text
Post "https://api.github.com/graphql": proxyconnect tcp: dial tcp 127.0.0.1:10808: connect: operation not permitted
gh auth status: The token in default is invalid.
```

没有创建远程地址，也没有反复重试；仓库保留在本地，工作树干净。
