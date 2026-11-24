"""PDF upload extraction, batch import, and audit job query endpoints."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.api.deps import get_auth_context, get_db_pool, get_pipeline, require_manager_or_above
from app.core.exceptions import NotFoundError
from app.core.security import AuthContext
from app.db.pool import DatabasePool
from app.models.schemas import ExtractionJobListItem, JobResponse
from app.services.extraction_pipeline import ExtractionPipeline

router = APIRouter(prefix="/extract", tags=["PDF Extraction"])


@router.get("/supported-reports")
async def list_supported_reports(
    pipeline: ExtractionPipeline = Depends(get_pipeline),
    _: AuthContext = Depends(get_auth_context),
) -> dict:
    """List all built-in PDF report extractors registered in this microservice."""
    return {
        "success": True,
        "data": pipeline.registry.list_supported_types(),
    }


@router.post("/upload", response_model=dict)
async def extract_and_import_pdf(
    file: UploadFile = File(..., description="Back-office report PDF file"),
    auto_import: bool = Form(default=True),
    deduplicate: bool = Form(default=False),
    overwrite_submitted: bool | None = Form(default=None),
    store_id_override: UUID | None = Form(default=None),
    shift_date_override: date | None = Form(default=None),
    shift_type_override: Literal["Shift 1", "Shift 2"] | None = Form(default=None),
    auth: AuthContext = Depends(require_manager_or_above),
    pipeline: ExtractionPipeline = Depends(get_pipeline),
) -> dict:
    """Upload a single PDF report, extract selective fields, and optionally auto-import into daily_shifts."""
    pdf_bytes = await file.read()
    effective_store_override = store_id_override or (
        UUID(auth.store_id) if auth.store_id and auth.role.lower() not in {"admin", "superadmin", "system"} else None
    )

    result: JobResponse = await pipeline.process_pdf(
        pdf_bytes,
        filename=file.filename or "report.pdf",
        source_type="upload",
        organization_id=auth.organization_id,
        store_id_override=effective_store_override,
        shift_date_override=shift_date_override,
        shift_type_override=shift_type_override,
        auto_import=auto_import,
        deduplicate=deduplicate,
        overwrite_submitted=overwrite_submitted,
    )
    return {
        "success": True,
        "data": result.model_dump(mode="json"),
    }


@router.post("/batch", response_model=dict)
async def extract_and_import_batch(
    files: list[UploadFile] = File(..., description="Multiple back-office PDF reports"),
    auto_import: bool = Form(default=True),
    deduplicate: bool = Form(default=False),
    store_id_override: UUID | None = Form(default=None),
    shift_date_override: date | None = Form(default=None),
    shift_type_override: Literal["Shift 1", "Shift 2"] | None = Form(default=None),
    auth: AuthContext = Depends(require_manager_or_above),
    pipeline: ExtractionPipeline = Depends(get_pipeline),
) -> dict:
    """Upload multiple PDF reports (e.g. Sales & Labor + Kitchen Speed) and merge them into shifts."""
    results: list[dict] = []
    errors: list[dict] = []

    for upload in files:
        content = await upload.read()
        try:
            job = await pipeline.process_pdf(
                content,
                filename=upload.filename or "report.pdf",
                source_type="upload",
                organization_id=auth.organization_id,
                store_id_override=store_id_override,
                shift_date_override=shift_date_override,
                shift_type_override=shift_type_override,
                auto_import=auto_import,
                deduplicate=deduplicate,
            )
            results.append(job.model_dump(mode="json"))
        except Exception as exc:
            errors.append({"filename": upload.filename, "error": str(exc)})

    return {
        "success": len(errors) == 0,
        "data": {
            "processed_count": len(results),
            "error_count": len(errors),
            "results": results,
            "errors": errors,
        },
    }


@router.get("/jobs")
async def list_extraction_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    report_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> dict:
    """List recent PDF extraction and import audit jobs."""
    clauses: list[str] = ["1=1"]
    params: list = []

    if auth.organization_id and auth.role.lower() != "system":
        clauses.append("(organization_id IS NULL OR organization_id = %s)")
        params.append(auth.organization_id)
    if status_filter:
        clauses.append("status = %s")
        params.append(status_filter)
    if report_type:
        clauses.append("report_type = %s")
        params.append(report_type)

    params.append(limit)
    sql = f"""
        SELECT id, source_type, filename, report_type, report_date, shift_type,
               status, store_id, target_daily_shift_id, email_subject, email_sender,
               processing_ms, created_at
        FROM extractor_jobs
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC
        LIMIT %s
    """
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, tuple(params))
            rows = await cur.fetchall()

    items = [ExtractionJobListItem.model_validate(r).model_dump(mode="json") for r in rows]
    return {"success": True, "data": items}


@router.get("/jobs/{job_id}")
async def get_extraction_job(
    job_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> dict:
    """Fetch complete details for a single extraction job."""
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT *
                FROM extractor_jobs
                WHERE id = %s
                LIMIT 1
                """,
                (str(job_id),),
            )
            row = await cur.fetchone()

    if not row:
        raise NotFoundError(f"Extraction job '{job_id}' not found")
    if (
        auth.organization_id
        and row.get("organization_id")
        and str(row["organization_id"]) != str(auth.organization_id)
    ):
        raise NotFoundError(f"Extraction job '{job_id}' not found")

    return {"success": True, "data": row}
