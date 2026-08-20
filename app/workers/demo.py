from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.services.catalog import CatalogService


DEMO_SEED = 20260821


def generate_demo_tables(rows: int = 1_200, seed: int = DEMO_SEED) -> dict[str, pd.DataFrame]:
    """Generate a deterministic, explicitly synthetic multi-table credit-risk sample."""

    if rows < 500:
        raise ValueError("DEMO_ROWS_MINIMUM_500")
    rng = np.random.default_rng(seed)
    customer_count = max(400, int(rows * 0.76))
    customer_ids = np.array([f"C{index:06d}" for index in range(customer_count)])
    order_customers = rng.choice(customer_ids, size=rows, replace=True)
    order_ids = np.array([f"O{index:07d}" for index in range(rows)])
    application_dates = pd.date_range("2023-01-01", "2025-12-31", periods=rows)

    age_by_customer = rng.integers(21, 61, customer_count)
    income_by_customer = np.exp(rng.normal(9.0, 0.48, customer_count)).round(0)
    employment_by_customer = rng.choice(
        ["受薪", "个体", "自由职业", "其他"],
        size=customer_count,
        p=[0.58, 0.2, 0.15, 0.07],
    )
    region_by_customer = rng.choice(["华东", "华南", "华北", "西部"], customer_count)
    customer_index = {value: index for index, value in enumerate(customer_ids)}
    order_customer_index = np.array([customer_index[value] for value in order_customers])

    prior_delinquencies = rng.poisson(0.42, rows)
    bureau_inquiries_30d = rng.poisson(1.5, rows)
    debt_ratio = np.clip(rng.beta(2.2, 4.3, rows), 0.01, 0.98)
    device_risk = np.clip(rng.beta(1.4, 6.0, rows), 0, 1)
    account_age_days = rng.integers(1, 2500, rows)
    contact_stability = np.clip(rng.normal(0.72, 0.19, rows), 0, 1)
    age = age_by_customer[order_customer_index]
    income = income_by_customer[order_customer_index]
    employment = employment_by_customer[order_customer_index]

    linear = (
        -2.25
        + 0.72 * prior_delinquencies
        + 1.35 * debt_ratio
        + 1.15 * device_risk
        + 0.13 * bureau_inquiries_30d
        - 0.000018 * income
        - 0.012 * (age - 35)
        + np.where(employment == "自由职业", 0.22, 0)
        - 0.35 * contact_stability
        + rng.normal(0, 0.42, rows)
    )
    probability = 1 / (1 + np.exp(-linear))
    fpd0 = rng.binomial(1, probability)
    fpd7 = rng.binomial(1, np.clip(probability * 1.2, 0, 0.95)).astype(float)
    mob30 = rng.binomial(1, np.clip(probability * 1.45, 0, 0.97)).astype(float)
    recent = np.arange(rows) >= rows - max(80, rows // 8)
    fpd7[recent & (np.arange(rows) % 2 == 0)] = -1
    fpd7[recent & (np.arange(rows) % 2 == 1)] = np.nan
    mob30[np.arange(rows) >= rows - max(150, rows // 5)] = np.nan
    mob30[(np.arange(rows) >= rows - max(300, rows // 3)) & (np.arange(rows) % 3 == 0)] = -1

    base = pd.DataFrame(
        {
            "order_id": order_ids,
            "customer_id": order_customers,
            "application_date": application_dates.strftime("%Y-%m-%d"),
            "FPD0": fpd0,
            "FPD7": fpd7,
            "MOB30": mob30,
        }
    )
    demographics = pd.DataFrame(
        {
            "customer_id": customer_ids,
            "age": age_by_customer,
            "monthly_income": income_by_customer,
            "employment_type": employment_by_customer,
            "region_group": region_by_customer,
        }
    )
    bureau = pd.DataFrame(
        {
            "order_id": order_ids,
            "prior_delinquencies": prior_delinquencies,
            "bureau_inquiries_30d": bureau_inquiries_30d,
            "debt_ratio": debt_ratio.round(5),
            "post_collection_status": rng.choice(["none", "contacted"], rows),
        }
    )
    device = pd.DataFrame(
        {
            "order_id": order_ids,
            "device_risk_index": device_risk.round(5),
            "account_age_days": account_age_days,
            "contact_stability": contact_stability.round(5),
            "sparse_external_value": np.where(rng.random(rows) < 0.18, rng.normal(size=rows), np.nan),
        }
    )
    descriptions = {
        "order_id": ("订单唯一标识", "", "identifier"),
        "customer_id": ("客户唯一标识", "", "identifier"),
        "application_date": ("申请日期", "", "time"),
        "FPD0": ("首期当日逾期标签", "-1", "target"),
        "FPD7": ("首期七日逾期标签", "-1", "target"),
        "MOB30": ("账龄三十日表现标签", "-1", "target"),
        "age": ("申请时年龄", "", "feature"),
        "monthly_income": ("月收入", "-999", "feature"),
        "employment_type": ("就业类型", "未知", "feature"),
        "region_group": ("区域分组", "", "feature"),
        "prior_delinquencies": ("申请前历史逾期次数", "-1", "feature"),
        "bureau_inquiries_30d": ("近三十日查询次数", "-1", "feature"),
        "debt_ratio": ("负债收入比", "-999", "feature"),
        "post_collection_status": ("贷后催收状态，仅用于演示泄漏阻断", "", "post_outcome"),
        "device_risk_index": ("设备风险指数", "-999", "feature"),
        "account_age_days": ("账户存续天数", "-1", "feature"),
        "contact_stability": ("联系方式稳定度", "-999", "feature"),
        "sparse_external_value": ("外部稀疏特征", "-999", "feature"),
    }
    dictionary = pd.DataFrame(
        [
            {"字段名": name, "字段含义": values[0], "缺失码": values[1], "字段角色": values[2]}
            for name, values in descriptions.items()
        ]
    )
    return {
        "base": base,
        "demographics": demographics,
        "bureau": bureau,
        "device": device,
        "dictionary": dictionary,
    }


def install_demo_project(
    catalog: CatalogService,
    *,
    name: str = "多表风控建模演示",
    mode: str = "semi_trusted",
    rows: int = 1_200,
    seed: int = DEMO_SEED,
) -> dict[str, Any]:
    project = catalog.create_project(
        name,
        "固定种子合成数据；只用于验证产品流程与技术正确性，不代表真实业务效果。",
        mode,
        {"synthetic_demo": True, "seed": seed, "rows": rows},
    )
    tables = generate_demo_tables(rows, seed)
    with tempfile.TemporaryDirectory(prefix="risk-agent-demo-") as temporary:
        root = Path(temporary)
        workbook = root / "synthetic_loan_book.xlsx"
        with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
            tables["base"].to_excel(writer, sheet_name="放款订单", index=False)
            tables["dictionary"].to_excel(writer, sheet_name="字段说明", index=False)
        source_files = {
            "demographics": root / "synthetic_demographics.csv",
            "bureau": root / "synthetic_bureau.csv",
            "device": root / "synthetic_device.csv",
        }
        for key, path in source_files.items():
            tables[key].to_csv(path, index=False, encoding="utf-8-sig")
        base = catalog.register_asset(
            project["id"], workbook, workbook.name, "base", "放款订单",
            {"synthetic_demo": True},
        )
        dictionary = catalog.register_asset(
            project["id"], workbook, workbook.name, "dictionary", "字段说明",
            {"synthetic_demo": True},
        )
        assets = [base]
        for key in ("demographics", "bureau", "device"):
            asset = catalog.register_asset(
                project["id"], source_files[key], source_files[key].name, "feature",
                metadata={"synthetic_demo": True},
            )
            assets.append(asset)
        for asset in assets:
            catalog.attach_dictionary(asset["id"], dictionary["id"])

    plan = catalog.create_join_plan(
        project["id"],
        "订单主表 + 三张特征表",
        base["id"],
        [
            {
                "right_asset_id": assets[1]["id"],
                "left_keys": ["customer_id"],
                "right_keys": ["customer_id"],
                "how": "left",
                "expected_cardinality": "many_to_one",
                "suffix": "_demographic",
            },
            {
                "right_asset_id": assets[2]["id"],
                "left_keys": ["order_id"],
                "right_keys": ["order_id"],
                "how": "left",
                "expected_cardinality": "many_to_one",
                "suffix": "_bureau",
            },
            {
                "right_asset_id": assets[3]["id"],
                "left_keys": ["order_id"],
                "right_keys": ["order_id"],
                "how": "left",
                "expected_cardinality": "many_to_one",
                "suffix": "_device",
            },
        ],
    )
    plan, dataset = catalog.execute_join_plan(
        plan["id"], ["FPD0", "FPD7", "MOB30"], "customer_id"
    )
    tasks = [
        catalog.create_target_task(project["id"], dataset["id"], target)
        for target in ("FPD0", "FPD7", "MOB30")
    ]
    return {
        "project": project,
        "assets": [*assets, dictionary],
        "join_plan": plan,
        "dataset_version": dataset,
        "target_tasks": tasks,
        "synthetic_evidence": {"is_synthetic": True, "seed": seed, "rows": rows},
    }
