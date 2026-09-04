from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.runs import _public_run, _public_trace_bundle
from app.core.config import Settings
from app.core.errors import normalize_error_code, public_error_message
from app.main import create_app
from app.providers.gateway import ProviderGateway
from app.services.conversations import ConversationService


_SECRET = "sk-super-secret-value"
_PRIVATE_PATH = r"C:\Users\private-user\Documents\raw-data.xlsx"


def _raise_value_error(*_args, **_kwargs):
    raise ValueError(f"could not open {_PRIVATE_PATH} with {_SECRET}")


def _raise_runtime_error(*_args, **_kwargs):
    raise RuntimeError(f"third party failed at {_PRIVATE_PATH} using {_SECRET}")


def _raise_http_error(*_args, **_kwargs):
    raise HTTPException(
        502,
        detail=f"upstream failed at {_PRIVATE_PATH} using {_SECRET}",
        headers={"x-upstream-debug": _SECRET},
    )


def test_validation_and_unknown_http_errors_use_safe_chinese_envelope(app_paths):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        validation = client.put(
            "/api/v1/providers/settings",
            json={"api_key": [_SECRET], "memory_budget_mb": "not-a-number"},
        )
        missing = client.get("/api/v1/does-not-exist")
        method = client.put("/api/v1/projects", json={})

    assert validation.status_code == 422
    assert validation.json() == {
        "error": {
            "code": "REQUEST_VALIDATION_FAILED",
            "message": "请求参数格式不正确，请检查必填字段、类型和长度后重试。",
        }
    }
    serialized = validation.text
    assert _SECRET not in serialized
    assert "input" not in serialized.lower()
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ROUTE_NOT_FOUND"
    assert method.status_code == 405
    assert method.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_value_key_and_unhandled_exceptions_never_echo_raw_details(app_paths, monkeypatch):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        missing = client.get("/api/v1/projects/not-a-real-project")

        monkeypatch.setattr(app.state.context.catalog, "create_project", _raise_value_error)
        invalid = client.post("/api/v1/projects", json={"name": "异常边界"})

        monkeypatch.setattr(app.state.context.catalog, "create_project", _raise_runtime_error)
        internal = client.post("/api/v1/projects", json={"name": "未知异常"})

        monkeypatch.setattr(app.state.context.catalog, "create_project", _raise_http_error)
        upstream = client.post("/api/v1/projects", json={"name": "上游异常"})

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert "not-a-real-project" not in missing.text

    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
    assert _SECRET not in invalid.text and _PRIVATE_PATH not in invalid.text

    assert internal.status_code == 500
    assert internal.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert _SECRET not in internal.text and _PRIVATE_PATH not in internal.text
    assert "RuntimeError" not in internal.text

    assert upstream.status_code == 502
    assert upstream.json()["error"]["code"] == "HTTP_ERROR"
    assert _SECRET not in upstream.text and _PRIVATE_PATH not in upstream.text
    assert "x-upstream-debug" not in upstream.headers


def test_known_field_contract_error_keeps_code_but_not_exception_details(app_paths, monkeypatch):
    app = create_app(app_paths, auto_migrate=False)

    def missing_fields(*_args, **_kwargs):
        raise ValueError("MISSING_REQUIRED_FIELDS: ['mobile', 'id_number']")

    monkeypatch.setattr(app.state.context.catalog, "create_project", missing_fields)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/projects", json={"name": "字段契约"})

    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "MISSING_REQUIRED_FIELDS",
        "message": "评分数据缺少模型必需字段，请按模型字段契约补齐后重试。",
    }
    assert "mobile" not in response.text
    assert "id_number" not in response.text


def test_provider_connectivity_error_is_actionable_and_never_echoes_exception(app_paths):
    class FailingClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            raise OSError(f"connection failed at {_PRIVATE_PATH}; token={_SECRET}")

    gateway = ProviderGateway(
        settings=Settings(),
        api_key=_SECRET,
        client_factory=FailingClient,
        paths=app_paths,
    )
    result = gateway.connectivity_check()

    assert result.ok is False
    assert result.error_code == "PROVIDER_REQUEST_FAILED"
    assert result.error_message == public_error_message("PROVIDER_REQUEST_FAILED")
    assert "检查网络" in (result.error_message or "")
    assert _SECRET not in (result.error_message or "")
    assert _PRIVATE_PATH not in (result.error_message or "")


def test_conversation_and_run_failure_messages_hide_technical_errors():
    raw_error = f"open failed: {_PRIVATE_PATH} {_SECRET}"
    run = {
        "status": "failed",
        "stage": "modeling",
        "node": "train",
        "error": raw_error,
    }
    answer = ConversationService._fallback_answer(run, {}, "PROVIDER_REQUEST_FAILED")
    public_run = _public_run(run)

    assert "PROVIDER_REQUEST_FAILED" not in answer
    assert "错误码" not in answer
    assert _SECRET not in answer and _PRIVATE_PATH not in answer
    assert "检查网络" in answer
    assert "当前建模节点执行失败" in answer
    assert public_run["error"] == "RUN_EXECUTION_FAILED"
    assert public_run["error_message"] == public_error_message("RUN_EXECUTION_FAILED")
    assert _SECRET not in str(public_run) and _PRIVATE_PATH not in str(public_run)

    public_bundle = _public_trace_bundle(
        {
            "run": run,
            "spans": [{"error_code": raw_error}],
            "events": [{"evidence": {"error_code": raw_error}}],
        }
    )
    assert _SECRET not in str(public_bundle) and _PRIVATE_PATH not in str(public_bundle)
    assert public_bundle["run"]["error"] == "RUN_EXECUTION_FAILED"
    assert public_bundle["spans"][0]["error_code"] == "RUN_EXECUTION_FAILED"
    assert public_bundle["events"][0]["evidence"]["error_message"] == public_error_message(
        "RUN_EXECUTION_FAILED"
    )


def test_error_code_normalization_rejects_exception_text():
    assert normalize_error_code("TARGET_SINGLE_CLASS: detail") == "TARGET_SINGLE_CLASS"
    assert normalize_error_code(f"failed at {_PRIVATE_PATH}", "RUN_EXECUTION_FAILED") == (
        "RUN_EXECUTION_FAILED"
    )


def test_corrupted_excel_upload_has_specific_chinese_recovery_action(app_paths):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        project = client.post("/api/v1/projects", json={"name": "损坏文件提示"}).json()["project"]
        response = client.post(
            f"/api/v1/projects/{project['id']}/data-assets",
            data={"kind": "base"},
            files={
                "file": (
                    "broken.xlsx",
                    b"not-an-excel-workbook",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DATA_ASSET_READ_FAILED"
    assert "文件未损坏" in response.json()["error"]["message"]
    assert "BadZipFile" not in response.text


def test_common_workspace_and_scoring_errors_are_actionable_chinese():
    expected_fragments = {
        "WORKSPACE_PATH_TOO_BROAD": "专用文件夹",
        "WORKSPACE_CONFIGURED_BY_ENVIRONMENT": "重启应用",
        "MISSING_REQUIRED_FIELDS": "模型字段契约",
        "FIELD_TYPE_MISMATCH": "修正字段类型",
        "SCORE_INPUT_READ_FAILED": "重新上传",
        "REPORT_READ_FAILED": "重新生成报告",
    }
    for code, fragment in expected_fragments.items():
        message = public_error_message(code)
        assert fragment in message
        assert code not in message


@pytest.mark.parametrize(
    ("code", "action"),
    [
        ("MISSING_REQUIRED_FIELDS", "补齐"),
        ("FIELD_TYPE_MISMATCH", "修正字段类型"),
        ("UNSUPPORTED_TABLE_FORMAT", "CSV"),
        ("EXCEL_SHEET_SELECTION_REQUIRED", "选择"),
        ("NO_SUCCESSFUL_MODELS", "检查数据"),
        ("UNSUPPORTED_OUTPUT_FORMAT", "输出格式"),
        ("SCORE_OUTPUT_CHECKSUM_MISMATCH", "重新评分"),
        ("ARTIFACT_FILE_MISSING", "重新生成"),
    ],
)
def test_frequent_import_model_and_scoring_codes_are_actionable_chinese(code: str, action: str):
    message = public_error_message(code)
    assert action in message
    assert code not in message
