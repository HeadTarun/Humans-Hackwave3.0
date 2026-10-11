"""Server entrypoint script for local development and cloud deployments."""
import os
import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    host = os.environ.get("HOST", settings.host)
    port = int(os.environ.get("PORT", settings.port))
    reload = settings.is_development()

    print(f"Starting HealTest API on {host}:{port} (reload={reload}, env={settings.app_env})")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
