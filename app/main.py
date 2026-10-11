"""FastAPI application factory."""
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.middleware.request_id import RequestIdMiddleware, get_request_id
from app.routes import projects, repairs, runs, tests
from app.routes import repository as repo_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="HealTest",
        description="Intent-aware self-healing E2E testing platform",
        version="0.1.0",
    )

    # Middleware (RequestId first, CORS outer)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes
    app.include_router(projects.router, prefix="/projects", tags=["projects"])
    app.include_router(repo_routes.router, prefix="/projects", tags=["repository"])
    app.include_router(tests.router, tags=["tests"])
    app.include_router(runs.router, tags=["runs"])
    app.include_router(repairs.router, tags=["repairs"])

    # Global error handler — add request_id to error responses
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "request_id": get_request_id()},
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).exception(
            "Unhandled exception",
            extra={"request_id": get_request_id(), "path": request.url.path},
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": get_request_id()},
        )

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        return {
            "status": "ok",
            "version": "0.1.0",
            "environment": settings.app_env,
            "services": {
                "supabase": settings.is_supabase_configured(),
                "e2b": settings.is_e2b_configured(),
                "groq": bool(settings.groq_api_key),
            },
        }

    return app


app = create_app()
