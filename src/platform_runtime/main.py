import logging

import uvicorn

from .settings import settings

APPLICATIONS = {
    "analytics": "analytics_agent.app:app",
    "knowledge-api": "knowledge_graph_agent.app:app",
    "knowledge-worker": "knowledge_graph_agent.worker:app",
    "registry": "registry_service.app:app",
    "supervisor": "supervisor_service.app:app",
    "weather": "weather_agent.app:app",
}


def main() -> None:
    logging.basicConfig(level=settings.log_level)
    if settings.service not in APPLICATIONS:
        raise SystemExit(f"Unknown SERVICE={settings.service}; choose {', '.join(APPLICATIONS)}")
    uvicorn.run(APPLICATIONS[settings.service], host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
