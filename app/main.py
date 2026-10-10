"""FastAPI application factory."""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.middleware.request_id import RequestIdMiddleware, get_request_id
from app.routes import projects, repairs, runs, tests
from app.routes import repository as repo_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="HealTest",
        description="Intent-aware self-healing E2E testing platform",
        version="0.1.0",
    )

    # Middleware
    app.add_middleware(RequestIdMiddleware)

    # Routes
    app.include_router(projects.router, prefix="/projects", tags=["projects"])
    app.include_router(repo_routes.router, prefix="/projects", tags=["repository"])
    app.include_router(tests.router, tags=["tests"])
    app.include_router(runs.router, tags=["runs"])
    app.include_router(repairs.router, tags=["repairs"])

    # Global error handler — add request_id to all error responses
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
        return {"status": "ok", "version": "0.1.0"}

    return app


app = create_app()
