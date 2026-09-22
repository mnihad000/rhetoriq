from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from config import get_settings
from services.embedding_service import get_embedding_service

router = APIRouter()


def readiness_report(settings=None) -> dict[str, Any]:
    """Check only dependencies required for safe API service."""
    settings = settings or get_settings()
    dependencies: dict[str, dict[str, str]] = {}

    try:
        from migrations.runner import verify_migrations
        verify_migrations(settings.DATABASE_URL)
        dependencies["postgresql"] = {"status": "ready"}
    except Exception as exc:
        dependencies["postgresql"] = {"status": "unavailable", "reason": type(exc).__name__}

    try:
        from services.kafka_runtime import KafkaEventPublisher
        health = KafkaEventPublisher(settings=settings).health()
        dependencies["kafka"] = {
            "status": "ready" if health.get("status") == "ready" else "unavailable"
        }
    except Exception as exc:
        dependencies["kafka"] = {"status": "unavailable", "reason": type(exc).__name__}

    try:
        from events.topics import check_topics
        check_topics()
        dependencies["topics_and_schemas"] = {"status": "ready"}
    except Exception as exc:
        dependencies["topics_and_schemas"] = {"status": "unavailable", "reason": type(exc).__name__}

    if settings.ENABLE_B5_RETRIEVAL:
        try:
            from events.b5_init import check as check_b5
            from services.b5_cache import B5Cache
            check_b5()
            cache = B5Cache(
                settings.REDIS_URL,
                ttl_seconds=settings.B5_CACHE_TTL_SECONDS,
                max_item_bytes=settings.B5_CACHE_MAX_BYTES,
                password=settings.REDIS_PASSWORD,
                ca_cert=settings.REDIS_CA_CERT,
            )
            if not cache.available:
                raise RuntimeError("Redis is unavailable")
            dependencies["b5"] = {"status": "ready"}
        except Exception as exc:
            dependencies["b5"] = {"status": "unavailable", "reason": type(exc).__name__}

    ready = all(item["status"] == "ready" for item in dependencies.values())
    return {"status": "ready" if ready else "unavailable", "dependencies": dependencies}


@router.get("/health/ready")
def readiness_check():
    report = readiness_report()
    return JSONResponse(report, status_code=200 if report["status"] == "ready" else 503)


@router.get("/health/b5")
def b5_health() -> dict:
    """Dependency readiness is separate from the process liveness route."""
    settings = get_settings()
    if not settings.ENABLE_B5_RETRIEVAL:
        return {"status": "disabled", "enabled": False}
    try:
        from api.b5 import get_b5_service
        service = get_b5_service()
        durable = service.repository.status()
        dependencies = {}
        for name, target in (("elasticsearch", service.elastic), ("neo4j", service.neo4j)):
            dependencies[name] = target.health() if target else {"status": "unconfigured"}
            dependencies[name].pop("details", None)
            dependencies[name].pop("error", None)
        dependencies["redis"] = {"status": "healthy" if service.cache.available else "unavailable"}
        return {"status": "ready" if all(item["status"] == "healthy" for item in dependencies.values()) else "degraded",
                "enabled": True, "dependencies": dependencies, "projections": durable,
                "queries": {"latency":service.query_metrics,"cache":service.cache.status()},
                "event_health_url": "/api/research/health"}
    except Exception as exc:
        return {"status": "degraded", "enabled": True, "reason": type(exc).__name__}


@router.get("/health")
def health_check() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "demo_mode": settings.DEMO_MODE,
        "version": "0.1.0",
    }


@router.get("/health/embeddings")
def embedding_health() -> dict:
    service = get_embedding_service()
    try:
        service.load_model()
        return {
            "status": "ok",
            "model": service.model_name,
            "dimension": service.dimension,
            "cache_enabled": service.cache_enabled,
        }
    except Exception as exc:
        return {
            "status": "error",
            "model": service.model_name,
            "dimension": service.dimension,
            "cache_enabled": service.cache_enabled,
            "error": str(exc),
        }
