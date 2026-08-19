# 技术架构

```text
浏览器（原生 HTML/CSS/JS）
        │ 仅访问本机 127.0.0.1
        ▼
FastAPI API ── SQLite：项目状态、Run 元数据、追加式审计事件
        │
        ├── Storage：项目隔离目录、CSV 哈希、原子 JSON 写入、路径校验
        ├── Profiling：pandas 聚合画像、字段治理与启发式泄漏提醒
        ├── Planning：版本化 Plan、阻断项、确认清单、审批哈希
        ├── ML Worker：训练分区 OOF → 候选比较 → Champion → 留出集
        └── Reporting / Rule Agent：只读取结果 JSON，不重新计算或编造数字
```

## 状态门禁

`uploaded → profiled → awaiting_approval → approved → training → completed`

训练失败会进入 `failed`，保留失败 Run 元数据，但不会沿用旧指标。修改数据或方案会清除旧审批和当前 Run 绑定；相同方案重新提交不会重复要求确认。

## 训练隔离

- 数值字段使用训练分区拟合的中位数填补；逻辑回归使用训练分区标准化。
- 类别字段使用训练分区众数填补和 `handle_unknown` 独热编码。
- 候选模型为 Dummy、逻辑回归和随机森林。
- 候选比较与 KS 阈值选择只读取训练分区 OOF 预测；留出集只在最终阶段打开。
- 运行结果仅保存聚合指标、方案和复现信息，不保存逐行预测。

## 运行产物

每个项目的数据与产物默认位于本地 `instance/projects/<project_id>/`，包含 CSV、画像、方案、Run 结果、HTML 报告和模型文件；`instance/` 已加入 Git 忽略。数据库只保存可检索的元数据与审计事件。
