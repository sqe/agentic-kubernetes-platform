import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

TASKS = Counter("agent_tasks_total", "Tasks processed", ["agent", "status"])
ACTIVE = Gauge("agent_active_tasks", "Tasks currently processing", ["agent"])
DURATION = Histogram("agent_task_duration_seconds", "Task processing duration", ["agent"])

logger = logging.getLogger(__name__)


@contextmanager
def observe_task(agent: str) -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"status": "error"}
    started = time.monotonic()
    ACTIVE.labels(agent=agent).inc()
    try:
        yield state
    finally:
        ACTIVE.labels(agent=agent).dec()
        TASKS.labels(agent=agent, status=state["status"]).inc()
        DURATION.labels(agent=agent).observe(time.monotonic() - started)


def trace_execution(
    agent: str, request_id: str, result: dict[str, Any], tracking_uri: str | None
) -> None:
    if not tracking_uri:
        return
    try:
        import mlflow

        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(f"{agent}-executions")
        with mlflow.start_run(run_name=request_id):
            mlflow.log_param("request_id", request_id)
            mlflow.log_param("status", "error" if "error" in result else "success")
            mlflow.log_dict(result, "result.json")
    except Exception:
        logger.exception("MLflow trace failed", extra={"request_id": request_id})
