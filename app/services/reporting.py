"""Self-contained HTML model report generated only from deterministic artifacts."""

from __future__ import annotations

from html import escape
from typing import Any, Dict, Iterable, List

BOUNDARY = (
    "本报告仅反映指定数据集及切分方式下的离线实验结果，不代表未来业务表现、"
    "生产可用性、业务收益、公平性或监管合规性。"
)


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "不可计算"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return escape(str(value))


def _warning_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("message") or item.get("code") or item)
    return str(item)


def _rows(items: Iterable[Iterable[Any]]) -> str:
    return "".join(
        "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>" for row in items
    )


def render_model_report(
    project: Dict[str, Any], plan: Dict[str, Any], result: Dict[str, Any]
) -> str:
    metrics = result.get("holdout_metrics") or {}
    champion = result.get("champion") or {}
    candidates = result.get("candidate_comparison") or []
    lift = metrics.get("lift_table") or result.get("lift_table") or []
    importance = result.get("feature_importance") or []
    warnings: List[Any] = list(plan.get("warnings") or []) + list(result.get("warnings") or [])
    target = plan.get("target") or {}
    split = plan.get("split") or {}
    reproducibility = result.get("reproducibility") or {}
    is_demo = bool(project.get("dataset_is_demo") or (project.get("dataset") or {}).get("is_demo"))

    candidate_rows = []
    for item in candidates:
        candidate_rows.append(
            [
                item.get("display_name") or item.get("name", "-"),
                _fmt(item.get("roc_auc")),
                _fmt(item.get("ks")),
                _fmt(item.get("average_precision") or item.get("pr_auc")),
            ]
        )

    lift_rows = []
    for item in lift:
        lift_rows.append(
            [
                item.get("decile", "-"),
                item.get("count", "-"),
                item.get("positives", "-"),
                _fmt(item.get("positive_rate")),
                _fmt(item.get("cumulative_capture_rate")),
                _fmt(item.get("lift")),
            ]
        )

    importance_rows = []
    for item in importance[:20]:
        importance_rows.append(
            [
                item.get("feature", "-"),
                _fmt(item.get("importance") if "importance" in item else item.get("value")),
            ]
        )

    warning_items = "".join(f"<li>{escape(_warning_text(item))}</li>" for item in warnings)
    demo_banner = (
        '<div class="banner demo">演示数据：本次结果只能证明框架可以运行，不构成真实业务验证。</div>'
        if is_demo
        else ""
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(project.get("name", "风控建模项目"))} · 离线实验报告</title>
  <style>
    :root {{ color-scheme: light; --ink:#18212f; --muted:#667085; --line:#dde3ea; --brand:#2357d8; --paper:#fff; --bg:#f3f6fa; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ width:min(1060px, calc(100% - 32px)); margin:32px auto; background:var(--paper); padding:44px; border:1px solid var(--line); border-radius:18px; }}
    h1 {{ margin:0 0 6px; font-size:30px; }} h2 {{ margin:34px 0 12px; font-size:19px; }}
    .meta {{ color:var(--muted); }} .grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:20px 0; }}
    .metric {{ padding:16px; border:1px solid var(--line); border-radius:12px; }} .metric b {{ display:block; font-size:22px; margin-top:4px; }}
    .banner {{ padding:14px 16px; border-radius:10px; margin:18px 0; background:#fff4e5; border:1px solid #f2c66d; }}
    .boundary {{ background:#edf3ff; border-color:#aec4f5; }}
    table {{ width:100%; border-collapse:collapse; }} th,td {{ padding:10px 12px; border-bottom:1px solid var(--line); text-align:left; }} th {{ color:var(--muted); font-weight:600; }}
    code {{ overflow-wrap:anywhere; }} ul {{ padding-left:20px; }}
    @media(max-width:720px) {{ main {{ padding:24px; }} .grid {{ grid-template-columns:repeat(2,1fr); }} }}
  </style>
</head>
<body><main>
  <p class="meta">Risk Model Agent · 确定性本地 Worker 产物</p>
  <h1>{escape(project.get("name", "风控建模项目"))}</h1>
  <p class="meta">Run ID：<code>{escape(str(result.get("run_id", "-")))}</code></p>
  {demo_banner}
  <div class="banner boundary"><strong>证据边界：</strong>{escape(BOUNDARY)}</div>

  <h2>实验摘要</h2>
  <div class="grid">
    <div class="metric">冠军模型<b>{
        escape(str(champion.get("display_name") or champion.get("name", "-")))
    }</b></div>
    <div class="metric">ROC-AUC<b>{_fmt(metrics.get("roc_auc"))}</b></div>
    <div class="metric">KS<b>{_fmt(metrics.get("ks"))}</b></div>
    <div class="metric">PR-AUC<b>{
        _fmt(metrics.get("average_precision") or metrics.get("pr_auc"))
    }</b></div>
    <div class="metric">Brier Score<b>{_fmt(metrics.get("brier_score"))}</b></div>
    <div class="metric">Precision<b>{_fmt(metrics.get("precision"))}</b></div>
    <div class="metric">Recall<b>{_fmt(metrics.get("recall"))}</b></div>
    <div class="metric">F1<b>{_fmt(metrics.get("f1"))}</b></div>
  </div>

  <h2>批准方案</h2>
  <table><tbody>
    {
        _rows(
            [
                ["目标字段", target.get("column", "-")],
                ["坏样本取值", target.get("positive_label", "-")],
                ["纳入字段数", len((plan.get("features") or {}).get("included_columns") or [])],
                ["切分方式", split.get("method", "-")],
                ["留出比例", split.get("test_size", "-")],
                ["随机种子", split.get("random_state", "-")],
                [
                    "数据集 SHA-256",
                    (project.get("dataset") or {}).get(
                        "sha256", project.get("dataset_sha256", "-")
                    ),
                ],
                ["批准方案 SHA-256", result.get("plan_hash", "-")],
            ]
        )
    }
  </tbody></table>

  <h2>训练集 OOF 候选比较</h2>
  <table><thead><tr><th>候选</th><th>ROC-AUC</th><th>KS</th><th>PR-AUC</th></tr></thead><tbody>
    {_rows(candidate_rows)}
  </tbody></table>

  <h2>留出集 Lift</h2>
  <table><thead><tr><th>十分位</th><th>样本</th><th>坏样本</th><th>坏样本率</th><th>累计捕获率</th><th>Lift</th></tr></thead><tbody>
    {_rows(lift_rows)}
  </tbody></table>

  <h2>Top 特征贡献</h2>
  <p class="meta">系数或重要性仅说明当前模型中的统计贡献，不代表因果关系。</p>
  <table><thead><tr><th>变换后特征</th><th>绝对贡献值</th></tr></thead><tbody>
    {_rows(importance_rows)}
  </tbody></table>

  <h2>风险与限制</h2>
  <ul>{warning_items or "<li>当前规则未生成额外警告；这不代表不存在模型风险。</li>"}</ul>

  <h2>可复现信息</h2>
  <table><tbody>{_rows([[key, value] for key, value in reproducibility.items()])}</tbody></table>
</main></body></html>"""
