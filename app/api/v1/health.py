"""Health and readiness endpoints for container orchestrators and monitoring."""

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.api.deps import get_db_pool, get_ingestor, get_pipeline, get_scheduler
from app.core.config import Settings, get_settings
from app.db.pool import DatabasePool
from app.services.email_ingestor import EmailIngestor
from app.services.extraction_pipeline import ExtractionPipeline
from app.services.scheduler import MailboxPollingScheduler

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live")
async def liveness(settings: Settings = Depends(get_settings)) -> dict:
    """Fast liveness check confirming process responsiveness."""
    return {
        "success": True,
        "service": settings.SERVICE_NAME,
        "version": settings.SERVICE_VERSION,
        "status": "alive",
    }


@router.get("/ready")
async def readiness(
    db_pool: DatabasePool = Depends(get_db_pool),
    ingestor: EmailIngestor = Depends(get_ingestor),
    pipeline: ExtractionPipeline = Depends(get_pipeline),
    scheduler: MailboxPollingScheduler = Depends(get_scheduler),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Readiness probe reporting PostgreSQL, IMAP, scheduler, and extractor registry health."""
    db_status = await db_pool.health_check()
    imap_status = await ingestor.test_connection()
    is_ready = bool(db_status.get("connected"))

    payload = {
        "success": is_ready,
        "service": settings.SERVICE_NAME,
        "version": settings.SERVICE_VERSION,
        "database": db_status,
        "imap": imap_status,
        "scheduler": {
            "enabled": settings.IMAP_ENABLED,
            "running": scheduler.is_running,
            "poll_interval_seconds": settings.POLL_INTERVAL_SECONDS,
        },
        "supported_reports": pipeline.registry.list_supported_types(),
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
    )
