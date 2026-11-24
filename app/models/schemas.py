"""Pydantic v2 schemas for API requests, extracted payloads, rules, and shift mapping."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DurationParts(BaseModel):
    """Normalized minutes + seconds duration compatible with daily_shifts.service_metrics."""

    raw: str
    minutes: int = Field(ge=0)
    seconds: int = Field(ge=0, le=59)
    total_seconds: int = Field(ge=0)


class MappedShiftFields(BaseModel):
    """Selective shift-entry fields mapped to RestoOrbit daily_shifts columns."""

    store_code: str | None = None
    store_name_hint: str | None = None
    shift_date: date | None = None
    shift_type: Literal["Shift 1", "Shift 2"] | None = None
    net_sales: float | None = Field(default=None, ge=0)
    labor_cost: float | None = Field(default=None, ge=0)
    labor_percent: float | None = Field(default=None, ge=0)
    shift_minutes: int | None = Field(default=None, ge=0)
    shift_seconds: int | None = Field(default=None, ge=0, le=59)
    peak_minutes: int | None = Field(default=None, ge=0)
    peak_seconds: int | None = Field(default=None, ge=0, le=59)
    perfect_time_seconds: int | None = Field(default=None, ge=0)
    pulled_cars: int | None = Field(default=None, ge=0)
    trainees_count: int | None = Field(default=None, ge=0)
    this_year_sales: float | None = Field(default=None, ge=0)
    last_year_sales: float | None = Field(default=None, ge=0)
    notes: str | None = None


class ExtractionResult(BaseModel):
    """Structured output returned by any PDF report extractor."""

    report_type: str
    report_title: str
    confidence: float = Field(ge=0.0, le=1.0)
    store_code: str | None = None
    store_name: str | None = None
    report_date: date | None = None
    generated_at_raw: str | None = None
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    mapped_shift: MappedShiftFields = Field(default_factory=MappedShiftFields)
    warnings: list[str] = Field(default_factory=list)


class ShiftImportOutcome(BaseModel):
    """Result of applying extracted fields to a daily_shifts record."""

    imported: bool
    action: Literal["created_draft", "updated_existing", "skipped_submitted", "dry_run", "skipped_no_store"]
    daily_shift_id: UUID | None = None
    store_id: UUID | None = None
    store_name: str | None = None
    shift_date: date | None = None
    shift_type: Literal["Shift 1", "Shift 2"] | None = None
    updated_fields: dict[str, Any] = Field(default_factory=dict)
    message: str


class JobResponse(BaseModel):
    """Public representation of an extraction job record."""

    job_id: UUID | None = None
    status: str
    source_type: str
    filename: str
    file_sha256: str
    report_type: str
    processing_ms: int
    extraction: ExtractionResult
    shift_import: ShiftImportOutcome | None = None


class FieldMappingRule(BaseModel):
    """Declarative selective field extraction rule for custom PDFs."""

    target_field: Literal[
        "net_sales",
        "labor_cost",
        "labor_percent",
        "shift_minutes",
        "shift_seconds",
        "peak_minutes",
        "peak_seconds",
        "perfect_time_seconds",
        "pulled_cars",
        "trainees_count",
        "this_year_sales",
        "last_year_sales",
        "notes",
        "store_code",
        "shift_date",
    ]
    strategy: Literal["regex", "table_cell", "duration_table_cell"]
    pattern: str | None = None
    row_label: str | None = None
    column_header: str | None = None
    value_type: Literal["currency", "float", "int", "percent", "duration", "date", "string"] = "string"


class CreateExtractionRuleRequest(BaseModel):
    """Create or update a configurable report extraction rule."""

    name: str = Field(min_length=2, max_length=255)
    report_key: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9_]+$")
    match_keywords: list[str] = Field(min_length=1)
    sender_pattern: str | None = None
    subject_pattern: str | None = None
    default_shift_type: Literal["Shift 1", "Shift 2"] | None = None
    field_mappings: list[FieldMappingRule] = Field(min_length=1)


class EmailPollRequest(BaseModel):
    """Optional parameters when manually triggering an inbox poll."""

    mailbox: str | None = None
    search_criteria: str | None = None
    max_messages: int | None = Field(default=None, ge=1, le=200)
    auto_import: bool = True
    shift_type_override: Literal["Shift 1", "Shift 2"] | None = None
    store_id_override: UUID | None = None


class EmailPollSummary(BaseModel):
    """Summary returned after polling the IMAP inbox."""

    messages_scanned: int
    pdfs_found: int
    pdfs_processed: int
    pdfs_imported: int
    pdfs_skipped_duplicate: int
    pdfs_failed: int
    jobs: list[dict[str, Any]] = Field(default_factory=list)


class ExtractionJobListItem(BaseModel):
    """Summary item for listing extraction jobs."""

    id: UUID
    source_type: str
    filename: str
    report_type: str
    report_date: date | None = None
    shift_type: str | None = None
    status: str
    store_id: UUID | None = None
    target_daily_shift_id: UUID | None = None
    email_subject: str | None = None
    email_sender: str | None = None
    processing_ms: int | None = None
    created_at: datetime
