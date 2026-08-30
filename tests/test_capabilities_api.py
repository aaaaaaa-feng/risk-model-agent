from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.capabilities import router as capabilities_router
from app.main import create_app


EXPECTED_ALGORITHMS = {
    "dummy",
    "scorecard",
    "regularized_logistic",
    "random_forest",
    "extra_trees",
    "xgboost",
    "lightgbm",
    "catboost",
}


def test_capabilities_api_reports_model_adapters_and_notebook_packages(app_paths):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "risk-model-agent-capabilities/v1"
    assert payload["api_version"] == "v1"

    algorithms = {item["id"]: item for item in payload["algorithms"]}
    assert set(algorithms) == EXPECTED_ALGORITHMS
    assert all(item["label"] for item in algorithms.values())
    assert all(item["backend"] for item in algorithms.values())
    assert all(isinstance(item["available"], bool) for item in algorithms.values())
    assert all(isinstance(item["dependencies"], list) for item in algorithms.values())

    notebook = payload["notebook"]
    assert notebook["runtime"] == "jupyter"
    assert isinstance(notebook["available"], bool)
    assert set(notebook["dependencies"]) == {
        "pandas",
        "numpy",
        "duckdb",
        "nbformat",
        "jupyter_client",
        "ipykernel",
    }
    assert "polars" not in notebook["dependencies"]
    assert all(isinstance(available, bool) for available in notebook["dependencies"].values())


def test_capabilities_route_is_get_only():
    routes = [
        route
        for route in capabilities_router.routes
        if isinstance(route, APIRoute) and route.path == "/capabilities"
    ]

    assert len(routes) == 1
    assert routes[0].methods == {"GET"}


def test_capability_probe_does_not_import_heavy_data_or_model_libraries():
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
from app.api.capabilities import get_capabilities

get_capabilities()
heavy_modules = ("pandas", "numpy", "duckdb", "xgboost", "lightgbm", "catboost")
loaded = [name for name in heavy_modules if name in sys.modules]
if loaded:
    raise SystemExit("eager imports: " + ",".join(loaded))
"""
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
