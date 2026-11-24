"""Secure IMAP/SSL email inbox ingestor for extracting PDF attachments from back-office emails."""

import asyncio
import email
import imaplib
from dataclasses import dataclass, field
from datetime import datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class EmailPdfAttachment:
    """In-memory PDF attachment extracted from an email message."""

    filename: str
    content: bytes
    size_bytes: int


@dataclass
class IngestedEmailMessage:
    """Email envelope metadata along with its extracted PDF attachments."""

    uid: str
    message_id: str | None
    subject: str
    sender: str
    received_at: datetime | None
    attachments: list[EmailPdfAttachment] = field(default_factory=list)


def _decode_mime_header(raw_value: str | None) -> str:
    if not raw_value:
        return ""
    decoded_parts: list[str] = []
    for part, charset in decode_header(raw_value):
        if isinstance(part, bytes):
            decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded_parts.append(str(part))
    return "".join(decoded_parts).strip()


class EmailIngestor:
    """Fetches matching emails over IMAP and extracts PDF attachments in-memory."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _connect(self) -> imaplib.IMAP4:
        if self._settings.IMAP_USE_SSL:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                self._settings.IMAP_HOST,
                self._settings.IMAP_PORT,
                timeout=20,
            )
        else:
            client = imaplib.IMAP4(
                self._settings.IMAP_HOST,
                self._settings.IMAP_PORT,
                timeout=20,
            )
        client.login(self._settings.IMAP_USER, self._settings.IMAP_PASSWORD)
        return client

    def _poll_sync(
        self,
        mailbox: str,
        search_criteria: str,
        max_messages: int,
    ) -> list[IngestedEmailMessage]:
        if not self._settings.IMAP_USER or not self._settings.IMAP_PASSWORD:
            raise ValueError("IMAP_USER and IMAP_PASSWORD must be configured to poll email")

        results: list[IngestedEmailMessage] = []
        client = self._connect()
        try:
            status_code, _ = client.select(mailbox, readonly=not self._settings.IMAP_MARK_AS_READ)
            if status_code != "OK":
                raise RuntimeError(f"Failed to select IMAP mailbox '{mailbox}'")

            typ, data = client.uid("search", None, search_criteria)
            if typ != "OK" or not data or not data[0]:
                return results

            uids = data[0].split()
            # Process most recent messages up to max_messages
            target_uids = uids[-max_messages:]

            sender_filter = self._settings.IMAP_SENDER_FILTER.strip().lower()
            subject_filter = self._settings.IMAP_SUBJECT_FILTER.strip().lower()

            for uid_bytes in target_uids:
                uid_str = uid_bytes.decode("ascii", errors="ignore")
                fetch_typ, msg_data = client.uid("fetch", uid_bytes, "(RFC822)")
                if fetch_typ != "OK" or not msg_data:
                    continue

                raw_email = None
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        raw_email = response_part[1]
                        break
                if not raw_email:
                    continue

                msg = email.message_from_bytes(raw_email)
                subject = _decode_mime_header(msg.get("Subject"))
                sender = _decode_mime_header(msg.get("From"))
                message_id = _decode_mime_header(msg.get("Message-ID")) or None

                if sender_filter and sender_filter not in sender.lower():
                    continue
                if subject_filter and subject_filter not in subject.lower():
                    continue

                received_at = None
                raw_date = msg.get("Date")
                if raw_date:
                    try:
                        received_at = parsedate_to_datetime(raw_date)
                    except Exception:
                        received_at = None

                attachments: list[EmailPdfAttachment] = []
                for part in msg.walk():
                    if part.is_multipart():
                        continue
                    content_type = (part.get_content_type() or "").lower()
                    filename = _decode_mime_header(part.get_filename())
                    disposition = (part.get("Content-Disposition") or "").lower()

                    is_pdf = (
                        content_type == "application/pdf"
                        or filename.lower().endswith(".pdf")
                        or ("attachment" in disposition and filename.lower().endswith(".pdf"))
                    )
                    if not is_pdf:
                        continue

                    payload = part.get_payload(decode=True)
                    if payload:
                        attachments.append(
                            EmailPdfAttachment(
                                filename=filename or f"email_{uid_str}.pdf",
                                content=payload,
                                size_bytes=len(payload),
                            )
                        )

                if attachments:
                    results.append(
                        IngestedEmailMessage(
                            uid=uid_str,
                            message_id=message_id,
                            subject=subject,
                            sender=sender,
                            received_at=received_at,
                            attachments=attachments,
                        )
                    )
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass

        return results

    def _mark_processed_sync(self, mailbox: str, uids: list[str]) -> None:
        if not uids:
            return
        client = self._connect()
        try:
            client.select(mailbox, readonly=False)
            for uid in uids:
                if self._settings.IMAP_MARK_AS_READ:
                    client.uid("store", uid, "+FLAGS", r"(\Seen)")
                if self._settings.IMAP_MOVE_PROCESSED_FOLDER:
                    copy_res = client.uid("copy", uid, self._settings.IMAP_MOVE_PROCESSED_FOLDER)
                    if copy_res[0] == "OK":
                        client.uid("store", uid, "+FLAGS", r"(\Deleted)")
            if self._settings.IMAP_MOVE_PROCESSED_FOLDER:
                client.expunge()
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass

    async def fetch_pdf_emails(
        self,
        mailbox: str | None = None,
        search_criteria: str | None = None,
        max_messages: int | None = None,
    ) -> list[IngestedEmailMessage]:
        """Async wrapper around IMAP polling."""
        target_mailbox = mailbox or self._settings.IMAP_MAILBOX
        criteria = search_criteria or self._settings.IMAP_SEARCH_CRITERIA
        limit = max_messages or self._settings.POLL_MAX_MESSAGES_PER_RUN
        return await asyncio.to_thread(self._poll_sync, target_mailbox, criteria, limit)

    async def mark_messages_processed(self, uids: list[str], mailbox: str | None = None) -> None:
        """Mark processed UIDs as seen and optionally archive them."""
        if not uids:
            return
        target_mailbox = mailbox or self._settings.IMAP_MAILBOX
        await asyncio.to_thread(self._mark_processed_sync, target_mailbox, uids)

    async def test_connection(self) -> dict:
        """Verify IMAP credentials and connectivity without modifying messages."""
        if not self._settings.IMAP_ENABLED:
            return {"enabled": False, "connected": False, "reason": "IMAP_ENABLED=false"}
        if not self._settings.IMAP_USER or not self._settings.IMAP_PASSWORD:
            return {"enabled": True, "connected": False, "reason": "Missing IMAP credentials"}

        def _ping() -> dict:
            client = self._connect()
            try:
                typ, _ = client.select(self._settings.IMAP_MAILBOX, readonly=True)
                return {"enabled": True, "connected": typ == "OK", "mailbox": self._settings.IMAP_MAILBOX}
            finally:
                try:
                    client.logout()
                except Exception:
                    pass

        try:
            return await asyncio.to_thread(_ping)
        except Exception as exc:
            logger.warning("imap_health_check_failed", error=str(exc))
            return {"enabled": True, "connected": False, "error": str(exc)}
