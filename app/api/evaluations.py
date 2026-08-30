from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.evaluation.contracts import EvalSuite
from app.bootstrap import AppContext

from .dependencies import context


router = APIRouter(prefix="/evaluations", tags=["evaluation-harness"])


class EvaluationRunCreate(BaseModel):
    suite_id: str = Field(min_length=3, max_length=120)
    provider: dict[str, Any] | None = None
    baseline_run_id: str | None = None


@router.get("/suites")
def list_evaluation_suites(ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"suites": ctx.evaluations.list_suites()}


@router.post("/suites", status_code=201)
def save_evaluation_suite(payload: EvalSuite, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"suite": ctx.evaluations.save_suite(payload)}


@router.get("/suites/{suite_id}")
def get_evaluation_suite(suite_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"suite": ctx.evaluations.get_suite(suite_id)}


@router.post("/runs", status_code=202)
def start_evaluation_run(
    payload: EvaluationRunCreate, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    return {
        "run": ctx.evaluations.start_run(
            payload.suite_id,
            provider=payload.provider,
            baseline_run_id=payload.baseline_run_id,
        )
    }


@router.get("/runs")
def list_evaluation_runs(
    suite_id: str | None = None, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    return {"runs": ctx.evaluations.list_runs(suite_id)}


@router.get("/runs/{run_id}")
def get_evaluation_run(run_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {
        "run": ctx.evaluations.get_run(run_id),
        "results": ctx.evaluations.list_results(run_id),
    }
