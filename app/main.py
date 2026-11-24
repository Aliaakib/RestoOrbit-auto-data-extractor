"""FastAPI application factory and lifespan manager for Auto-Data-Extractor."""

import asyncio
import sys
import time
import uuid
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import v1_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.migrate import run_migrations
from app.db.pool import DatabasePool
from app.services.email_ingestor import EmailIngestor
from app.services.extraction_pipeline import ExtractionPipeline
from app.services.scheduler import MailboxPollingScheduler

logger = get_logger(__name__)


def create_app(custom_settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI microservice application."""
    settings = custom_settings or get_settings()
    configure_logging(settings.LOG_LEVEL, settings.ENV)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_pool = DatabasePool(settings)
        ingestor = EmailIngestor(settings)
        pipeline = ExtractionPipeline(db_pool, settings)
        scheduler = MailboxPollingScheduler(settings, pipeline, ingestor)

        app.state.settings = settings
        app.state.db_pool = db_pool
        app.state.ingestor = ingestor
        app.state.pipeline = pipeline
        app.state.scheduler = scheduler

        try:
            await db_pool.open()
            db_health = await db_pool.health_check()
            if db_health.get("connected"):
                await run_migrations(settings.DATABASE_URL)
            else:
                logger.warning("startup_db_unavailable", details=db_health)
        except Exception as exc:
            logger.warning("startup_db_init_warning", error=str(exc))

        scheduler.start()
        logger.info(
            "service_started",
            service=settings.SERVICE_NAME,
            port=settings.PORT,
            env=settings.ENV,
        )

        try:
            yield
        finally:
            await scheduler.stop()
            await db_pool.close()
            logger.info("service_stopped", service=settings.SERVICE_NAME)

    app = FastAPI(
        title="RestoOrbit Auto-Data-Extractor",
        version=settings.SERVICE_VERSION,
        description=(
            "Microservice for automated email inbox PDF ingestion, selective back-office "
            "report extraction, and atomic Shift Entry auto-import into RestoOrbit."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        started = time.perf_counter()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
        return response

    register_exception_handlers(app)
    app.include_router(v1_router)

    @app.get("/", tags=["Service"])
    async def root() -> dict:
        return {
            "service": settings.SERVICE_NAME,
            "version": settings.SERVICE_VERSION,
            "status": "online",
            "docs": "/docs",
            "endpoints": {
                "healthLive": "/api/v1/health/live",
                "healthReady": "/api/v1/health/ready",
                "supportedReports": "GET /api/v1/extract/supported-reports",
                "uploadPdf": "POST /api/v1/extract/upload",
                "batchUploadPdfs": "POST /api/v1/extract/batch",
                "extractionJobs": "GET /api/v1/extract/jobs",
                "mailboxStatus": "GET /api/v1/mailbox/status",
                "pollMailbox": "POST /api/v1/mailbox/poll",
                "rules": "GET, POST /api/v1/rules",
            },
        }

    return app


app = create_app()
