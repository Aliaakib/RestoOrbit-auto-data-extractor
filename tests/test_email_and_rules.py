"""Tests for dynamic GenericRuleExtractor, MIME/IMAP EmailIngestor, and ShiftImporter DB protection."""

from datetime import date
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.extractors.generic_rule_extractor import GenericRuleExtractor
from app.extractors.pdf_reader import read_pdf_document
from app.models.schemas import ExtractionResult, MappedShiftFields
from app.services.email_ingestor import EmailIngestor
from app.services.extraction_pipeline import ExtractionPipeline
from app.services.shift_importer import ShiftImporter
from tests.fixtures.pdf_factory import build_kitchen_speed_pdf, build_sales_and_labor_pdf


def test_generic_rule_extractor_selects_custom_fields():
    pdf_bytes = build_sales_and_labor_pdf()
    doc = read_pdf_document(pdf_bytes, filename="custom_sales.pdf")

    rule = {
        "report_key": "custom_dq_sales",
        "name": "Custom DQ Sales Rule",
        "default_shift_type": "Shift 2",
        "field_mappings": [
            {
                "target_field": "net_sales",
                "strategy": "regex",
                "pattern": r"Total\s+(\$[\d,]+\.\d{2})",
                "value_type": "currency",
            },
            {
                "target_field": "labor_percent",
                "strategy": "regex",
                "pattern": r"Total.*?(\d+\.\d{2}%)",
                "value_type": "percent",
            },
        ],
    }

    extractor = GenericRuleExtractor()
    result = extractor.extract_with_rule(doc, rule)
    assert result.report_type == "custom_dq_sales"
    assert result.mapped_shift.net_sales == 3754.12
    assert result.mapped_shift.labor_percent == 20.53
    assert result.mapped_shift.shift_type == "Shift 2"


@pytest.mark.asyncio
async def test_email_ingestor_extracts_pdf_attachments_and_polls_pipeline():
    settings = Settings(
        ENV="test",
        IMAP_ENABLED=True,
        IMAP_USER="reports@restoorbit.com",
        IMAP_PASSWORD="app-password",
    )

    # Build a real RFC822 MIME message containing two back-office PDF attachments
    mime_msg = EmailMessage()
    mime_msg["Subject"] = "Daily Back Office Reports - Shift 2"
    mime_msg["From"] = "backoffice@dq-pos.com"
    mime_msg["Message-ID"] = "<msg-001@dq-pos.com>"
    mime_msg.set_content("Attached are today's Sales & Labor and Kitchen Speed reports.")
    mime_msg.add_attachment(
        build_sales_and_labor_pdf(),
        maintype="application",
        subtype="pdf",
        filename="Sales_and_Labor_2026-11-24.pdf",
    )
    mime_msg.add_attachment(
        build_kitchen_speed_pdf(),
        maintype="application",
        subtype="pdf",
        filename="Kitchen_Speed_10912_2026-11-24.pdf",
    )
    raw_bytes = mime_msg.as_bytes()

    class FakeImapClient:
        def select(self, mailbox, readonly=False):
            return "OK", [b"1"]

        def uid(self, command, *args):
            if command == "search":
                return "OK", [b"101"]
            if command == "fetch":
                return "OK", [(b"101 (RFC822)", raw_bytes)]
            return "OK", []

        def close(self):
            return "OK", []

        def logout(self):
            return "BYE", []

    ingestor = EmailIngestor(settings)
    ingestor._connect = MagicMock(return_value=FakeImapClient())  # type: ignore[method-assign]

    mock_db = MagicMock()
    pipeline = ExtractionPipeline(mock_db, settings)
    # Bypass DB persistence in unit test by disabling persist_job or mocking _find_duplicate_job/_record_job
    pipeline._find_duplicate_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    pipeline._record_job = AsyncMock(return_value=uuid4())  # type: ignore[method-assign]

    summary = await pipeline.poll_and_process_inbox(ingestor, auto_import=False)
    assert summary["messages_scanned"] == 1
    assert summary["pdfs_found"] == 2
    assert summary["pdfs_processed"] == 2
    assert summary["pdfs_failed"] == 0
    assert summary["jobs"][0]["report_type"] == "sales_and_labor_by_hour"
    assert summary["jobs"][1]["report_type"] == "kitchen_speed_of_service_by_hour"


@pytest.mark.asyncio
async def test_shift_importer_skips_submitted_shift_by_default():
    settings = Settings(ENV="test", OVERWRITE_SUBMITTED_SHIFTS=False)

    fake_store = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "name": "10912 - Ellijay, GA - GC-B",
        "code": "10912",
        "shift_schedule": None,
    }
    fake_existing_shift = {
        "id": uuid4(),
        "status": "Submitted",
        "net_sales": 3000.00,
        "labor_cost": 600.00,
        "labor_percent": 20.00,
        "service_metrics": {"shiftMinutes": 5, "shiftSeconds": 10},
        "yearly_sales": None,
    }

    fake_cursor = MagicMock()
    fake_cursor.execute = AsyncMock()
    fake_cursor.fetchone = AsyncMock(side_effect=[fake_store, fake_existing_shift])
    fake_cursor.__aenter__ = AsyncMock(return_value=fake_cursor)
    fake_cursor.__aexit__ = AsyncMock(return_value=None)

    fake_conn = MagicMock()
    fake_conn.cursor = MagicMock(return_value=fake_cursor)
    fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_conn.__aexit__ = AsyncMock(return_value=None)

    fake_pool = MagicMock()
    fake_pool.connection = MagicMock(return_value=fake_conn)

    importer = ShiftImporter(fake_pool, settings)
    extraction = ExtractionResult(
        report_type="kitchen_speed_of_service_by_hour",
        report_title="Kitchen Speed of Service by Hour",
        confidence=0.98,
        store_code="10912",
        report_date=date(2026, 11, 24),
        mapped_shift=MappedShiftFields(
            store_code="10912",
            shift_date=date(2026, 11, 24),
            shift_type="Shift 2",
            shift_minutes=5,
            shift_seconds=39,
        ),
    )

    outcome = await importer.apply_extraction(extraction)
    assert outcome.imported is False
    assert outcome.action == "skipped_submitted"
    assert outcome.daily_shift_id == fake_existing_shift["id"]
