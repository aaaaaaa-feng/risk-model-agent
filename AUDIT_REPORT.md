# 风控建模 Agent 代码审计报告

- 审计对象：当前仓库 `risk-model-agent`
- 审计范围：数据导入、SafeEvidence/Provider、LangGraph 状态流、Worker、模型训练、报告导出、前端交互、测试与 Git 卫生
- 审计日期：2026-08-20
- 结论：本地 MVP 的关键安全和结果边界已通过；不能把它描述成已经完成跨平台安装、真实供应商验收或生产模型治理的平台。

## 已修复的高风险问题

1. 变量筛选原先可能在整表上计算 IV。现在筛选先冻结切分，再通过 `fit_positions` 只使用训练行，并在结果中写入 `fit_scope=train` 与 `fit_rows`。
2. 半信任流程原先在清洗确认后可能把界面阶段回写成 `planning`。现在清洗确认保持 `cleaning` 阶段，确认后从冻结状态进入 `screening`。
3. 确认卡原先只记录确认动作。现在 Y、切分方式和候选模型会校验、写入版本化方案，并实际影响筛选/训练；不支持的模型或不合规 Y 会拒绝。
4. 报告协议原先写成未实现的 `train_cv`。现在明确为 `train_fit → validation_select → oot_once`，指标协议版本为 `risk-validation/holdout-v1`。
5. 报告写入与 checksums 的顺序原先可能导致报告自身哈希过期。现在先固定产物清单，再写 JSON/HTML/XLSX，最后生成 checksums。
6. 多维画像原先可能先构造过大的笛卡尔积再截断。现在先做 Top-K/`<OTHER>`、分箱上限和组合数预估，超限返回 `GROUP_LIMIT_EXCEEDED`。
7. CSV 导入增加 UTF-8/GB18030 解码回退；XLSX 多 Sheet 必须明确选择，不再静默使用第一张表。
8. Provider 的单 Run/单月 token 预算不再只是界面字段：调用前检查额度，返回 usage 后写入本地 `provider_usage` 审计表，超限返回 `PROVIDER_BUDGET_EXCEEDED`。

## 已验证证据

- `17 passed`：单元、Worker、API、半信任恢复、XLSX Sheet、Provider DLP/预算、报告导出、训练集筛选和高维分析守卫。
- `ruff check app tests`：通过。
- `node --check app/static/app.js`：通过。
- `git diff --check`：通过。
- 本地 Git 提交：
  - `00dced8 feat: complete guarded modeling workflow`
  - `9a2feb6 fix: bound training selection and segment analysis`
- 外部 Provider 默认关闭；启用后只发送别名化 SafeEvidence，测试覆盖敏感载荷阻断和 OpenAI-compatible 请求形态。
- 生成代码只作为本地交付物，产品不执行任意生成代码；静态 Reviewer 阻断不会被标成成功。
- 当前验证使用 FastAPI TestClient 和本地 Worker 子进程；本环境不把一次受限沙箱端口绑定失败当作应用功能失败，也未把它包装成真实浏览器/跨平台验收。

## 仍明确未完成、不能宣称的事项

- 尚未用真实供应商 API 做连通性、限流、费用和服务条款验收；`llm_enabled` 是显式开关。
- Reviewer 已实现最多三轮“冻结方案 → 受控模板重生成 → 重审”，并保留 `generated_model_v1/v2/v3.py`；它不是任意自然语言代码的语义修复器，三轮仍未通过就阻断。
- 当前是固定训练/验证/OOT 留出协议，不是训练集内交叉验证或超参数搜索。
- 清洗目前自动执行的只有安全标准化；去重、异常截断、稀有类别合并、删除样本仍只生成方案并等待策略实现。
- 评分卡是 one-hot Logistic 代理产物，不是生产级 WOE 分箱评分卡；Baseline/what-if、PSI、相关性和数据字典仍属后续版本。
- 尚未生成或验收 macOS/Windows 安装包，也没有把普通 Web 启动方式冒充“拿来即用”的桌面发行版。
- 独立评测 Harness 按约定后置；当前仅保留 Trace 和普通软件回归测试。

## GitHub 尝试结果

按用户授权尝试执行公开仓库创建和推送，但当前 GitHub CLI 认证 token 已失效，且沙箱代理连接被拒绝：

```text
Post "https://api.github.com/graphql": proxyconnect tcp: dial tcp 127.0.0.1:10808: connect: operation not permitted
gh auth status: The token in default is invalid.
```

没有创建远程地址，也没有反复重试；仓库保留在本地，工作树干净。
