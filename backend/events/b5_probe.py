"""Worker readiness checks durable heartbeat; liveness is process-owned."""
from datetime import datetime, timezone
import socket
from config import get_settings
from services.b5_repository import ProjectionRepository


def main():
    settings=get_settings()
    status=ProjectionRepository(settings.persistence_target).status()
    worker_id=f"b5-{settings.B5_WORKER_TARGET}-{socket.gethostname()}"
    worker=next((w for w in status["workers"] if w["worker_id"]==worker_id),None)
    if not worker or worker["state"]!="ready":
        raise SystemExit(1)
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(worker["heartbeat_at"])).total_seconds()
    raise SystemExit(0 if age<45 else 1)


if __name__=="__main__":
    main()
