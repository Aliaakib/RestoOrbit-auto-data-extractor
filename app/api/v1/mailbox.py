"""Email mailbox polling and status endpoints."""

from fastapi import APIRouter, Depends

from app.api.deps import (
    get_auth_context,
    get_ingestor,
    get_pipeline,
    get_scheduler,
    require_manager_or_above,
)
from app.core.config import Settings, get_settings
from app.core.security import AuthContext
from app.models.schemas import EmailPollRequest
from app.services.email_ingestor import EmailIngestor
from app.services.extraction_pipeline import ExtractionPipeline
from app.services.scheduler import MailboxPollingScheduler

router = APIRouter(prefix="/mailbox", tags=["Email Mailbox"])


@router.get("/status")
async def mailbox_status(
    ingestor: EmailIngestor = Depends(get_ingestor),
    scheduler: MailboxPollingScheduler = Depends(get_scheduler),
    settings: Settings = Depends(get_settings),
    _: AuthContext = Depends(get_auth_context),
) -> dict:
    """Check IMAP connection status and background polling schedule."""
    conn_status = await ingestor.test_connection()
    return {
        "success": True,
        "data": {
            "imap": conn_status,
            "scheduler_running": scheduler.is_running,
            "poll_interval_seconds": settings.POLL_INTERVAL_SECONDS,
            "mailbox": settings.IMAP_MAILBOX,
            "search_criteria": settings.IMAP_SEARCH_CRITERIA,
        },
    }


@router.post("/poll")
async def poll_mailbox_now(
    payload: EmailPollRequest | None = None,
    auth: AuthContext = Depends(require_manager_or_above),
    ingestor: EmailIngestor = Depends(get_ingestor),
    pipeline: ExtractionPipeline = Depends(get_pipeline),
) -> dict:
    """Trigger an immediate IMAP mailbox scan for new PDF reports and auto-import them."""
    req = payload or EmailPollRequest()
    summary = await pipeline.poll_and_process_inbox(
        ingestor,
        organization_id=auth.organization_id,
        mailbox=req.mailbox,
        search_criteria=req.search_criteria,
        max_messages=req.max_messages,
        auto_import=req.auto_import,
        shift_type_override=req.shift_type_override,
        store_id_override=req.store_id_override,
    )
    return {
        "success": True,
        "data": summary,
    }
