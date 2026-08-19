"""FastAPI application factory and local development entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router
from app.config import PROJECT_ROOT, Settings
from app.config import settings as default_settings
from app.db import Database
from app.domain import DomainError
from app.services.storage import Storage


def create_app(custom_settings: Optional[Settings] = None) -> FastAPI:
    active_settings = custom_settings or default_settings
    database = Database(active_settings.database_path)
    storage = Storage(active_settings.instance_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        yield

    application = FastAPI(
        title="Risk Model Agent",
        version="0.1.0",
        description="Local-first, human-approved binary credit-risk modeling workflow.",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )
    application.state.database = database
    application.state.storage = storage
    application.state.settings = active_settings

    @application.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})

    @application.exception_handler(KeyError)
    async def key_error_handler(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {"code": "NOT_FOUND", "message": "Resource was not found.", "details": {}}
            },
        )

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    application.include_router(router)
    application.mount(
        "/static",
        StaticFiles(directory=str(PROJECT_ROOT / "app" / "static")),
        name="static",
    )
    templates = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "templates"))

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={})

    return application


# Imported by uvicorn and tests.
app = create_app()
