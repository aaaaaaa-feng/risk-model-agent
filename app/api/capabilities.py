from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.workers.model_adapters import model_capabilities


router = APIRouter(tags=["system"])

SCHEMA_VERSION = "risk-model-agent-capabilities/v2"


class AlgorithmCapability(BaseModel):
    """算法适配器在当前安装环境中的可用性。"""

    # 内部注册表可继续扩展元数据；对外只序列化本契约定义的字段。
    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    backend: str
    available: bool
    dependencies: list[str]


class CapabilitiesResponse(BaseModel):
    """对前端和安装包自检稳定的只读能力契约。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    api_version: str
    algorithms: list[AlgorithmCapability]


@router.get("/capabilities", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse:
    """返回当前安装实际可用的模型能力。"""

    return CapabilitiesResponse(
        schema_version=SCHEMA_VERSION,
        api_version="v1",
        algorithms=[AlgorithmCapability.model_validate(item) for item in model_capabilities()],
    )
