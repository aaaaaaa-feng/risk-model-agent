# 09｜Trace 与后续评测接入协议

## 1. 当前范围

V1 只建设可评测的事件、证据和导出接口，不内置独立评测 Harness 平台。后续评测平台作为单独项目，通过稳定标识和结构化 Trace 接入。

## 2. 标识

必须稳定关联 `project_id`、`data_asset_id`、`dataset_version_id`、`join_plan_id`、`target_task_id`、`run_id`、`checkpoint_id`、`decision_id`、`review_record_id`、`model_version_id`、`artifact_id`、`conversation_id`、`event_id`。

## 3. SSE 事件

Run、Agent、Tool、Reviewer 和对话使用可重连 SSE。事件至少包含：

```json
{
  "id": "event_xxx",
  "sequence": 12,
  "run_id": "run_xxx",
  "stage": "screening",
  "node": "screen_features",
  "agent": "local_worker",
  "tool": "screen_features",
  "status": "completed",
  "summary": "Train-only 变量筛选完成",
  "time": "2026-08-21T00:00:00Z",
  "evidence": {"checkpoint": true}
}
```

客户端使用事件序号去重和续传。事件摘要是可展示结论，不是隐藏思维链。

## 4. ReviewRecord

记录审核范围、轮次、状态、结构化问题、建议修复、确定性证据、Provider/模型和 SafeEvidence payload 哈希。不得保存密钥、原始表或完整外发响应正文。

## 5. 未来评测可用事实

- 节点完成率、耗时、重试和安全降级。
- Agent 推荐与用户最终选择差异。
- Reviewer 问题类型、修复轮数和复发率。
- 无 LLM/不同 Provider 下的结构化结果差异。
- 指标、报告和重载评分的一致性。
- DLP、安全阻断、错误恢复和资源降级行为。

这些是机制证据；业务模型好坏、用户效率提升和生产收益仍需真实场景评测，不能由合成黄金测试代替。

## 6. 接口边界

V1 API 统一位于 `/api/v1`。未来 Harness 只读拉取脱敏 Trace Bundle 或使用用户明确导出的评测包，不直接访问原始项目目录。Schema 必须带版本；新增字段保持向后兼容，破坏性变更升级主版本。

## 7. 保留与删除

Trace 随项目本地长期保留，可进入加密迁移包。项目进入回收站不立即删除 Trace；永久删除需显式确认并记录本地审计事件。遥测关闭时不向产品方上传任何 Trace。
