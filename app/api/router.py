from fastapi import APIRouter

from .artifacts import router as artifacts_router
from .capabilities import router as capabilities_router
from .conversations import router as conversations_router
from .evaluations import router as evaluations_router
from .projects import router as projects_router
from .providers import router as providers_router
from .runs import router as runs_router
from .workspace import router as workspace_router


router = APIRouter()
router.include_router(capabilities_router)
router.include_router(projects_router)
router.include_router(runs_router)
router.include_router(artifacts_router)
router.include_router(providers_router)
router.include_router(conversations_router)
router.include_router(workspace_router)
router.include_router(evaluations_router)
