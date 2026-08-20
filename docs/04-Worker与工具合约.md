# 04｜本地 Worker 与工具合约

## 1. 定义

Worker 是本地确定性 Python 执行层，不是 Agent。它接受结构化输入，输出结构化结果和证据；失败返回稳定错误码，不让 LLM 猜测统计结果。

## 2. 工具组

- I/O：格式识别、安全文件名、编码/Sheet 读取、资源预估、按列/按行分批。
- Profiling：类型、缺失、唯一值、Y 候选、数据字典、诊断和 SafeEvidence 聚合。
- Joining：键推荐、基数验证、匹配率、重复、膨胀、Y 分布和血缘。
- Splitting：按时间 Train/Test/OOT 或随机分层；客户隔离；OOT 锁定。
- Screening：Train-only IV、缺失率、相关性、PII/主键/泄漏/其他 Y 阻断和带理由恢复。
- Binning：自动单调 WOE 分箱、人工分箱版本、单调验证和失效声明。
- Modeling：候选训练、CV、类别权重、校准、Test 选择、OOT 最终评估。
- Reporting：统一 JSON 事实源、Excel、单文件 HTML、哈希清单。
- Packaging/Scoring：模型原生格式、字段契约、版本锁定、独立加载和批量评分。
- Notebook：项目级本地 Kernel、逐单元执行、保存和输出数据版本校验。

## 3. 通用结果

工具结果必须包含业务输出以及适用的：

- `fit_scope` / `selection_scope`；
- 数据版本与行列数；
- 警告和阻断问题；
- 资源计划和降级原因；
- 版本、checksum、lineage 或 evidence reference。

不得只返回自然语言“成功”。

## 4. 资源策略

根据估算内存和可用内存决定列批次、行批次和模型并发。缺失率、画像和 IV 可按列分批后汇总；宽表不应因横向过宽而直接拒绝。资源不足时依次：减小批次、单模型顺序、延后高资源模型、明确失败。禁止静默抽样或删样本。

## 5. 模型公平比较

- 特征筛选、分箱、预处理、类别权重和校准只在 Train/CV 范围拟合。
- Test 用于模型和校准选择。
- OOT 在 Champion 冻结后才计算。
- Dummy 作为下限，不作为默认 Champion 候选优先级。
- 单个可选算法缺依赖或训练失败时隔离记录；其他模型继续。

## 6. Notebook 合约

Notebook 是本地 `.ipynb`，使用项目级 Kernel。预置 Pandas、Polars、DuckDB、Scikit-learn、XGBoost、LightGBM、CatBoost。用户可逐单元格执行或导入已有 Notebook。

导入 Notebook 输出前必须校验：文件位于项目目录、粒度与行数合理、重复/膨胀、所有候选 Y 的分布、字段契约和父版本血缘。Notebook 默认联网且不是安全沙箱。

## 7. 模型包

每个包必须包含模型文件、字段契约、预处理、评分参数、依赖版本锁定、评分脚本和 checksum。加载评分前校验 checksum；同一输入在训练后内存评分与重载评分必须一致。
