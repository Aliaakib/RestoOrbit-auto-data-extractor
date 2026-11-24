"""End-to-end extraction orchestrator: validate -> deduplicate -> parse -> extract -> import -> audit."""

import json
import time
from datetime import date
from typing import Any, Literal
from uuid import UUID

from app.core.config import Settings
from app.core.exceptions import UnsupportedReportError
from app.core.logging import get_logger
from app.core.security import validate_pdf_bytes
from app.db.pool import DatabasePool
from app.extractors import (
    GenericRuleExtractor,
    ReportExtractorRegistry,
    build_default_registry,
)
from app.extractors.pdf_reader import ParsedPdfDocument, read_pdf_document
from app.models.schemas import ExtractionResult, JobResponse, ShiftImportOutcome
from app.services.email_ingestor import EmailIngestor
from app.services.shift_importer import ShiftImporter
from app.utils.hashing import sha256_bytes

logger = get_logger(__name__)


class ExtractionPipeline:
    """Coordinates PDF extraction, dynamic rules, audit persistence, and shift auto-import."""

    def __init__(
        self,
        db_pool: DatabasePool,
        settings: Settings,
        registry: ReportExtractorRegistry | None = None,
    ) -> None:
        self._db = db_pool
        self._settings = settings
        self._registry = registry or build_default_registry()
        self._generic_extractor = GenericRuleExtractor()
        self._shift_importer = ShiftImporter(db_pool, settings)

    @property
    def registry(self) -> ReportExtractorRegistry:
        return self._registry

    async def _find_duplicate_job(self, file_sha256: str) -> dict[str, Any] | None:
        try:
            async with self._db.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT id, report_type, status, target_daily_shift_id,
                               extracted_data, mapped_shift_payload, store_id, report_date, shift_type
                        FROM extractor_jobs
                        WHERE file_sha256 = %s
                          AND status IN ('imported', 'extracted')
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (file_sha256,),
                    )
                    return await cur.fetchone()
        except Exception as exc:
            logger.debug("dedup_lookup_skipped", error=str(exc))
            return None

    async def _find_matching_rule(
        self,
        doc: ParsedPdfDocument,
        organization_id: UUID | str | None,
        email_sender: str | None = None,
        email_subject: str | None = None,
    ) -> dict[str, Any] | None:
        try:
            async with self._db.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT id, organization_id, name, report_key, match_keywords,
                               sender_pattern, subject_pattern, default_shift_type, field_mappings
                        FROM extractor_rules
                        WHERE is_active = 1
                          AND (organization_id IS NULL OR organization_id = %s)
                        ORDER BY organization_id NULLS LAST, updated_at DESC
                        """,
                        (str(organization_id) if organization_id else None,),
                    )
                    rules = await cur.fetchall()
        except Exception as exc:
            logger.debug("rule_lookup_skipped", error=str(exc))
            return None

        text_lower = doc.full_text.lower()
        for rule in rules:
            keywords = rule.get("match_keywords") or []
            if keywords and all(str(kw).lower() in text_lower for kw in keywords):
                if rule.get("sender_pattern") and email_sender:
                    if rule["sender_pattern"].lower() not in email_sender.lower():
                        continue
                if rule.get("subject_pattern") and email_subject:
                    if rule["subject_pattern"].lower() not in email_subject.lower():
                        continue
                return rule
        return None

    async def _record_job(
        self,
        *,
        organization_id: UUID | str | None,
        store_id: UUID | str | None,
        source_type: str,
        filename: str,
        file_sha256: str,
        file_size_bytes: int,
        report_type: str,
        report_date: date | None,
        shift_type: str | None,
        status: str,
        extracted_data: dict[str, Any],
        mapped_shift_payload: dict[str, Any],
        target_daily_shift_id: UUID | None,
        warnings: list[str],
        error_message: str | None,
        processing_ms: int,
        email_meta: dict[str, Any] | None = None,
    ) -> UUID | None:
        meta = email_meta or {}
        try:
            async with self._db.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO extractor_jobs (
                          organization_id, store_id, source_type,
                          email_message_id, email_uid, email_subject, email_sender, email_received_at,
                          filename, file_sha256, file_size_bytes,
                          report_type, report_date, shift_type, status,
                          extracted_data, mapped_shift_payload, target_daily_shift_id,
                          warnings, error_message, processing_ms
                        ) VALUES (
                          %s, %s, %s,
                          %s, %s, %s, %s, %s,
                          %s, %s, %s,
                          %s, %s, %s, %s,
                          %s::jsonb, %s::jsonb, %s,
                          %s::jsonb, %s, %s
                        )
                        RETURNING id
                        """,
                        (
                            str(organization_id) if organization_id else None,
                            str(store_id) if store_id else None,
                            source_type,
                            meta.get("message_id"),
                            meta.get("uid"),
                            meta.get("subject"),
                            meta.get("sender"),
                            meta.get("received_at"),
                            filename,
                            file_sha256,
                            file_size_bytes,
                            report_type,
                            report_date,
                            shift_type,
                            status,
                            json.dumps(extracted_data, default=str),
                            json.dumps(mapped_shift_payload, default=str),
                            str(target_daily_shift_id) if target_daily_shift_id else None,
                            json.dumps(warnings),
                            error_message,
                            processing_ms,
                        ),
                    )
                    row = await cur.fetchone()
                    return row["id"] if row else None
        except Exception as exc:
            logger.warning("job_audit_insert_failed", error=str(exc), filename=filename)
            return None

    async def process_pdf(
        self,
        pdf_bytes: bytes,
        filename: str,
        *,
        source_type: Literal["email", "upload", "api"] = "upload",
        organization_id: UUID | str | None = None,
        store_id_override: UUID | str | None = None,
        shift_date_override: date | None = None,
        shift_type_override: Literal["Shift 1", "Shift 2"] | None = None,
        auto_import: bool = True,
        deduplicate: bool = True,
        overwrite_submitted: bool | None = None,
        persist_job: bool = True,
        email_meta: dict[str, Any] | None = None,
    ) -> JobResponse:
        """Process a single PDF document end-to-end."""
        started = time.perf_counter()
        validate_pdf_bytes(pdf_bytes, filename, self._settings.max_pdf_size_bytes)
        digest = sha256_bytes(pdf_bytes)

        if deduplicate and persist_job:
            dup = await self._find_duplicate_job(digest)
            if dup:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return JobResponse(
                    job_id=dup["id"],
                    status="skipped_duplicate",
                    source_type=source_type,
                    filename=filename,
                    file_sha256=digest,
                    report_type=dup["report_type"],
                    processing_ms=elapsed_ms,
                    extraction=ExtractionResult(
                        report_type=dup["report_type"],
                        report_title=dup["report_type"],
                        confidence=1.0,
                        report_date=dup.get("report_date"),
                        extracted_data=dup.get("extracted_data") or {},
                    ),
                    shift_import=ShiftImportOutcome(
                        imported=False,
                        action="dry_run",
                        daily_shift_id=dup.get("target_daily_shift_id"),
                        store_id=dup.get("store_id"),
                        shift_date=dup.get("report_date"),
                        shift_type=dup.get("shift_type"),
                        message="Skipped duplicate PDF already processed with identical SHA-256",
                    ),
                )

        doc = read_pdf_document(pdf_bytes, filename=filename)

        extractor, score = self._registry.resolve(doc, filename)
        extraction: ExtractionResult | None = None

        if extractor is not None and score >= 0.45:
            extraction = extractor.extract(
                doc,
                filename=filename,
                default_shift_type=shift_type_override or self._settings.DEFAULT_SHIFT_TYPE,
            )
        else:
            rule = await self._find_matching_rule(
                doc,
                organization_id=organization_id,
                email_sender=(email_meta or {}).get("sender"),
                email_subject=(email_meta or {}).get("subject"),
            )
            if rule:
                extraction = self._generic_extractor.extract_with_rule(
                    doc,
                    rule=rule,
                    default_shift_type=shift_type_override,
                )

        if extraction is None:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if persist_job:
                await self._record_job(
                    organization_id=organization_id,
                    store_id=store_id_override,
                    source_type=source_type,
                    filename=filename,
                    file_sha256=digest,
                    file_size_bytes=len(pdf_bytes),
                    report_type="unknown",
                    report_date=None,
                    shift_type=shift_type_override,
                    status="failed",
                    extracted_data={},
                    mapped_shift_payload={},
                    target_daily_shift_id=None,
                    warnings=[],
                    error_message="Unrecognized PDF report format",
                    processing_ms=elapsed_ms,
                    email_meta=email_meta,
                )
            raise UnsupportedReportError(
                f"PDF '{filename}' did not match any built-in report extractor or active rule"
            )

        shift_outcome: ShiftImportOutcome | None = None
        if auto_import:
            shift_outcome = await self._shift_importer.apply_extraction(
                extraction,
                organization_id=organization_id,
                store_id_override=store_id_override,
                shift_date_override=shift_date_override,
                shift_type_override=shift_type_override,
                email_subject=(email_meta or {}).get("subject"),
                dry_run=False,
                overwrite_submitted=overwrite_submitted,
            )
        else:
            shift_outcome = await self._shift_importer.apply_extraction(
                extraction,
                organization_id=organization_id,
                store_id_override=store_id_override,
                shift_date_override=shift_date_override,
                shift_type_override=shift_type_override,
                email_subject=(email_meta or {}).get("subject"),
                dry_run=True,
            )

        job_status = "imported" if (shift_outcome and shift_outcome.imported) else "extracted"
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        job_id: UUID | None = None
        if persist_job:
            job_id = await self._record_job(
                organization_id=organization_id,
                store_id=shift_outcome.store_id if shift_outcome else store_id_override,
                source_type=source_type,
                filename=filename,
                file_sha256=digest,
                file_size_bytes=len(pdf_bytes),
                report_type=extraction.report_type,
                report_date=shift_outcome.shift_date if shift_outcome else extraction.report_date,
                shift_type=shift_outcome.shift_type if shift_outcome else extraction.mapped_shift.shift_type,
                status=job_status,
                extracted_data=extraction.extracted_data,
                mapped_shift_payload=extraction.mapped_shift.model_dump(mode="json"),
                target_daily_shift_id=shift_outcome.daily_shift_id if shift_outcome else None,
                warnings=extraction.warnings,
                error_message=None,
                processing_ms=elapsed_ms,
                email_meta=email_meta,
            )

        logger.info(
            "pdf_processed",
            filename=filename,
            report_type=extraction.report_type,
            status=job_status,
            processing_ms=elapsed_ms,
            imported=shift_outcome.imported if shift_outcome else False,
        )

        return JobResponse(
            job_id=job_id,
            status=job_status,
            source_type=source_type,
            filename=filename,
            file_sha256=digest,
            report_type=extraction.report_type,
            processing_ms=elapsed_ms,
            extraction=extraction,
            shift_import=shift_outcome,
        )

    async def poll_and_process_inbox(
        self,
        ingestor: EmailIngestor,
        *,
        organization_id: UUID | str | None = None,
        mailbox: str | None = None,
        search_criteria: str | None = None,
        max_messages: int | None = None,
        auto_import: bool = True,
        shift_type_override: Literal["Shift 1", "Shift 2"] | None = None,
        store_id_override: UUID | str | None = None,
    ) -> dict[str, Any]:
        """Poll IMAP mailbox for PDFs, process each attachment, and mark processed emails."""
        messages = await ingestor.fetch_pdf_emails(
            mailbox=mailbox,
            search_criteria=search_criteria,
            max_messages=max_messages,
        )

        pdfs_found = sum(len(m.attachments) for m in messages)
        pdfs_processed = 0
        pdfs_imported = 0
        pdfs_skipped_duplicate = 0
        pdfs_failed = 0
        jobs_summary: list[dict[str, Any]] = []
        processed_uids: list[str] = []

        for msg in messages:
            all_attachments_ok = True
            for attachment in msg.attachments:
                try:
                    result = await self.process_pdf(
                        attachment.content,
                        attachment.filename,
                        source_type="email",
                        organization_id=organization_id,
                        store_id_override=store_id_override,
                        shift_type_override=shift_type_override,
                        auto_import=auto_import,
                        deduplicate=True,
                        email_meta={
                            "uid": msg.uid,
                            "message_id": msg.message_id,
                            "subject": msg.subject,
                            "sender": msg.sender,
                            "received_at": msg.received_at,
                        },
                    )
                    if result.status == "skipped_duplicate":
                        pdfs_skipped_duplicate += 1
                    else:
                        pdfs_processed += 1
                        if result.shift_import and result.shift_import.imported:
                            pdfs_imported += 1

                    jobs_summary.append(
                        {
                            "job_id": str(result.job_id) if result.job_id else None,
                            "filename": result.filename,
                            "report_type": result.report_type,
                            "status": result.status,
                            "shift_import_action": (
                                result.shift_import.action if result.shift_import else None
                            ),
                        }
                    )
                except Exception as exc:
                    all_attachments_ok = False
                    pdfs_failed += 1
                    logger.error(
                        "email_attachment_processing_failed",
                        filename=attachment.filename,
                        subject=msg.subject,
                        error=str(exc),
                    )
                    jobs_summary.append(
                        {
                            "filename": attachment.filename,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )

            if all_attachments_ok:
                processed_uids.append(msg.uid)

        if processed_uids:
            await ingestor.mark_messages_processed(processed_uids, mailbox=mailbox)

        return {
            "messages_scanned": len(messages),
            "pdfs_found": pdfs_found,
            "pdfs_processed": pdfs_processed,
            "pdfs_imported": pdfs_imported,
            "pdfs_skipped_duplicate": pdfs_skipped_duplicate,
            "pdfs_failed": pdfs_failed,
            "jobs": jobs_summary,
        }
