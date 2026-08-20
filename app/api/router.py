from fastapi import APIRouter

from .artifacts import router as artifacts_router
from .conversations import router as conversations_router
from .notebooks import router as notebooks_router
from .projects import router as projects_router
from .providers import router as providers_router
from .runs import router as runs_router


router = APIRouter()
router.include_router(projects_router)
router.include_router(runs_router)
router.include_router(notebooks_router)
router.include_router(artifacts_router)
router.include_router(providers_router)
router.include_router(conversations_router)
