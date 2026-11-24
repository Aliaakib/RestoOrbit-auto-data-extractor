"""Async background scheduler for periodic IMAP inbox polling without overlapping runs."""

import asyncio
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.email_ingestor import EmailIngestor
from app.services.extraction_pipeline import ExtractionPipeline

logger = get_logger(__name__)


class MailboxPollingScheduler:
    """Runs periodic email polling in the background when IMAP_ENABLED=true."""

    def __init__(
        self,
        settings: Settings,
        pipeline: ExtractionPipeline,
        ingestor: EmailIngestor,
    ) -> None:
        self._settings = settings
        self._pipeline = pipeline
        self._ingestor = ingestor
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def run_once(self) -> dict[str, Any] | None:
        """Execute a single non-overlapping inbox poll cycle."""
        if self._lock.locked():
            logger.warning("mailbox_poll_skipped_already_running")
            return None

        async with self._lock:
            return await self._pipeline.poll_and_process_inbox(
                self._ingestor,
                auto_import=self._settings.AUTO_IMPORT_ENABLED,
            )

    async def _loop(self) -> None:
        logger.info(
            "mailbox_scheduler_started",
            interval_seconds=self._settings.POLL_INTERVAL_SECONDS,
            mailbox=self._settings.IMAP_MAILBOX,
        )
        while not self._stop_event.is_set():
            try:
                summary = await self.run_once()
                if summary is not None:
                    logger.info("mailbox_poll_cycle_completed", **summary)
            except Exception as exc:
                logger.exception("mailbox_poll_cycle_error", error=str(exc))

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._settings.POLL_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                continue

        logger.info("mailbox_scheduler_stopped")

    def start(self) -> None:
        if not self._settings.IMAP_ENABLED:
            logger.info("mailbox_scheduler_disabled", reason="IMAP_ENABLED=false")
            return
        if self.is_running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="imap-mailbox-poller")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
