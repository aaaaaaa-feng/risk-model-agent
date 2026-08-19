"""Deterministic synthetic data for framework-only demonstrations."""

from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta
from io import StringIO

SAMPLE_FILENAME = "risk_model_agent_demo.csv"


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def generate_sample_csv(rows: int = 800, seed: int = 20260819) -> bytes:
    """Create a reproducible, fictional application-level credit sample."""

    rng = random.Random(seed)
    output = StringIO(newline="")
    fieldnames = [
        "application_id",
        "application_date",
        "age",
        "monthly_income",
        "debt_to_income",
        "prior_delinquencies",
        "credit_history_months",
        "employment_type",
        "city_tier",
        "acquisition_channel",
        "has_mortgage",
        "bad_flag",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    start = date(2024, 1, 1)

    for index in range(rows):
        age = rng.randint(21, 61)
        income = max(2_000, int(rng.lognormvariate(8.85, 0.48)))
        debt_to_income = min(0.95, max(0.03, rng.betavariate(2.2, 4.8)))
        delinquencies = min(6, int(rng.expovariate(1.05)))
        history = max(2, int((age - 18) * 7.5 + rng.gauss(0, 26)))
        employment = rng.choices(
            ["salaried", "self_employed", "gig", "other"],
            weights=[0.55, 0.22, 0.17, 0.06],
            k=1,
        )[0]
        city_tier = rng.choices(["T1", "T2", "T3"], weights=[0.34, 0.42, 0.24], k=1)[0]
        channel = rng.choices(
            ["direct", "partner", "organic", "campaign"],
            weights=[0.35, 0.25, 0.22, 0.18],
            k=1,
        )[0]
        mortgage = rng.choice(["yes", "no"])

        log_odds = (
            -2.15
            + 2.35 * debt_to_income
            + 0.52 * delinquencies
            - 0.004 * history
            - 0.000025 * income
            + (0.42 if employment == "gig" else 0.0)
            + (0.28 if channel == "campaign" else 0.0)
            + (0.18 if mortgage == "no" else -0.08)
            + rng.gauss(0, 0.32)
        )
        bad_flag = 1 if rng.random() < _sigmoid(log_odds) else 0

        writer.writerow(
            {
                "application_id": f"DEMO-{index + 1:06d}",
                "application_date": (start + timedelta(days=index)).isoformat(),
                "age": age,
                "monthly_income": "" if rng.random() < 0.045 else income,
                "debt_to_income": round(debt_to_income, 4),
                "prior_delinquencies": delinquencies,
                "credit_history_months": history,
                "employment_type": employment,
                "city_tier": city_tier,
                "acquisition_channel": channel,
                "has_mortgage": mortgage,
                "bad_flag": bad_flag,
            }
        )

    return output.getvalue().encode("utf-8")
