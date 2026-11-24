"""Atomic selective importer that merges extracted PDF metrics into daily_shifts."""

import json
import re
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.pool import DatabasePool
from app.models.schemas import ExtractionResult, ShiftImportOutcome

logger = get_logger(__name__)

_DEFAULT_SERVICE_METRICS = {
    "peakMinutes": 15,
    "peakSeconds": 0,
    "shiftMinutes": 15,
    "shiftSeconds": 0,
    "perfectTimeSeconds": 15,
}


def resolve_shift_timing(
    shift_schedule: dict[str, Any] | None,
    shift_date: date,
    shift_type: str,
) -> str:
    """Resolve dynamic shift window string matching RestoOrbit backend conventions."""
    is_shift_1 = shift_type == "Shift 1"
    default_timing = "8:00 AM – 4:00 PM" if is_shift_1 else "4:00 PM – 12:00 AM"
    if not shift_schedule or not isinstance(shift_schedule, dict):
        return default_timing

    day_key = shift_date.strftime("%A").lower()
    day_cfg = shift_schedule.get(day_key)
    if not isinstance(day_cfg, dict):
        return default_timing

    shift_cfg = day_cfg.get("shift1" if is_shift_1 else "shift2")
    if isinstance(shift_cfg, dict) and shift_cfg.get("from") and shift_cfg.get("to"):
        return f"{shift_cfg['from']} – {shift_cfg['to']}"
    return default_timing


def infer_shift_type(
    explicit_override: Literal["Shift 1", "Shift 2"] | None,
    email_subject: str | None,
    mapped_shift_type: Literal["Shift 1", "Shift 2"] | None,
    default_shift_type: Literal["Shift 1", "Shift 2"],
) -> Literal["Shift 1", "Shift 2"]:
    """Determine target Shift 1 vs Shift 2 from explicit override, subject hints, or default."""
    if explicit_override:
        return explicit_override

    if email_subject:
        subj = email_subject.lower()
        if re.search(r"\bshift\s*1\b", subj) or re.search(r"\bam\s+shift\b", subj):
            return "Shift 1"
        if re.search(r"\bshift\s*2\b", subj) or re.search(r"\bpm\s+shift\b", subj):
            return "Shift 2"

    if mapped_shift_type:
        return mapped_shift_type

    return default_shift_type


class ShiftImporter:
    """Safely merges selective extracted fields into RestoOrbit's daily_shifts table."""

    def __init__(self, db_pool: DatabasePool, settings: Settings) -> None:
        self._db = db_pool
        self._settings = settings

    async def _resolve_store(
        self,
        conn: Any,
        organization_id: UUID | str | None,
        store_id_override: UUID | str | None,
        store_code: str | None,
        store_name_hint: str | None,
    ) -> dict[str, Any] | None:
        async with conn.cursor() as cur:
            if store_id_override:
                if organization_id:
                    await cur.execute(
                        """
                        SELECT id, organization_id, name, code, shift_schedule
                        FROM stores
                        WHERE id = %s AND organization_id = %s AND deleted_at IS NULL
                        LIMIT 1
                        """,
                        (str(store_id_override), str(organization_id)),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, organization_id, name, code, shift_schedule
                        FROM stores
                        WHERE id = %s AND deleted_at IS NULL
                        LIMIT 1
                        """,
                        (str(store_id_override),),
                    )
                row = await cur.fetchone()
                if row:
                    return row

            if store_code:
                if organization_id:
                    await cur.execute(
                        """
                        SELECT id, organization_id, name, code, shift_schedule
                        FROM stores
                        WHERE code = %s AND organization_id = %s AND deleted_at IS NULL AND is_active = 1
                        LIMIT 1
                        """,
                        (store_code, str(organization_id)),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, organization_id, name, code, shift_schedule
                        FROM stores
                        WHERE code = %s AND deleted_at IS NULL AND is_active = 1
                        LIMIT 2
                        """,
                        (store_code,),
                    )
                    rows = await cur.fetchall()
                    if len(rows) == 1:
                        return rows[0]
                    return None
                row = await cur.fetchone()
                if row:
                    return row

            if store_name_hint and store_name_hint.lower() != "all dq locations":
                pattern = f"%{store_name_hint}%"
                if organization_id:
                    await cur.execute(
                        """
                        SELECT id, organization_id, name, code, shift_schedule
                        FROM stores
                        WHERE name ILIKE %s AND organization_id = %s AND deleted_at IS NULL AND is_active = 1
                        LIMIT 2
                        """,
                        (pattern, str(organization_id)),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, organization_id, name, code, shift_schedule
                        FROM stores
                        WHERE name ILIKE %s AND deleted_at IS NULL AND is_active = 1
                        LIMIT 2
                        """,
                        (pattern,),
                    )
                rows = await cur.fetchall()
                if len(rows) == 1:
                    return rows[0]

            # If org has exactly one active store, resolve unambiguously when report says "All DQ Locations"
            if organization_id:
                await cur.execute(
                    """
                    SELECT id, organization_id, name, code, shift_schedule
                    FROM stores
                    WHERE organization_id = %s AND deleted_at IS NULL AND is_active = 1
                    LIMIT 2
                    """,
                    (str(organization_id),),
                )
                rows = await cur.fetchall()
                if len(rows) == 1:
                    return rows[0]

        return None

    async def _get_prior_safe_baseline(
        self,
        conn: Any,
        store_id: str,
        shift_date: date,
        shift_type: str,
    ) -> float:
        if shift_type == "Shift 1":
            prior_date = shift_date - timedelta(days=1)
            prior_type = "Shift 2"
        else:
            prior_date = shift_date
            prior_type = "Shift 1"

        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT safe_count_total
                FROM daily_shifts
                WHERE store_id = %s
                  AND shift_date = %s
                  AND shift_type = %s
                  AND deleted_at IS NULL
                LIMIT 1
                """,
                (store_id, prior_date, prior_type),
            )
            row = await cur.fetchone()
            if row and row.get("safe_count_total") is not None:
                return float(row["safe_count_total"])
        return 0.0

    @staticmethod
    def build_selective_updates(extraction: ExtractionResult) -> dict[str, Any]:
        """Build dictionary of only non-null extracted shift fields."""
        mapped = extraction.mapped_shift
        updates: dict[str, Any] = {}

        if mapped.net_sales is not None:
            updates["net_sales"] = round(float(mapped.net_sales), 2)
        if mapped.labor_cost is not None:
            updates["labor_cost"] = round(float(mapped.labor_cost), 2)
        if mapped.labor_percent is not None:
            updates["labor_percent"] = round(float(mapped.labor_percent), 2)
        if mapped.trainees_count is not None:
            updates["trainees_count"] = int(mapped.trainees_count)
        if mapped.pulled_cars is not None:
            updates["pulled_cars"] = int(mapped.pulled_cars)

        service_patch: dict[str, int] = {}
        if mapped.shift_minutes is not None:
            service_patch["shiftMinutes"] = int(mapped.shift_minutes)
        if mapped.shift_seconds is not None:
            service_patch["shiftSeconds"] = int(mapped.shift_seconds)
        if mapped.peak_minutes is not None:
            service_patch["peakMinutes"] = int(mapped.peak_minutes)
        if mapped.peak_seconds is not None:
            service_patch["peakSeconds"] = int(mapped.peak_seconds)
        if mapped.perfect_time_seconds is not None:
            service_patch["perfectTimeSeconds"] = int(mapped.perfect_time_seconds)
        if service_patch:
            updates["service_metrics"] = service_patch

        if mapped.this_year_sales is not None or mapped.last_year_sales is not None:
            updates["yearly_sales"] = {
                "thisYear": mapped.this_year_sales or 0.0,
                "lastYear": mapped.last_year_sales or 0.0,
            }

        if mapped.notes:
            updates["notes"] = mapped.notes

        return updates

    async def apply_extraction(
        self,
        extraction: ExtractionResult,
        *,
        organization_id: UUID | str | None = None,
        store_id_override: UUID | str | None = None,
        shift_date_override: date | None = None,
        shift_type_override: Literal["Shift 1", "Shift 2"] | None = None,
        email_subject: str | None = None,
        dry_run: bool = False,
        overwrite_submitted: bool | None = None,
    ) -> ShiftImportOutcome:
        """Upsert or selectively patch daily_shifts without clobbering existing drawer/safe data."""
        target_date = shift_date_override or extraction.mapped_shift.shift_date or extraction.report_date
        target_shift = infer_shift_type(
            shift_type_override,
            email_subject,
            extraction.mapped_shift.shift_type,
            self._settings.DEFAULT_SHIFT_TYPE,
        )
        selective_updates = self.build_selective_updates(extraction)

        if target_date is None:
            return ShiftImportOutcome(
                imported=False,
                action="skipped_no_store",
                shift_type=target_shift,
                updated_fields=selective_updates,
                message="Report date could not be determined; pass shift_date_override to import",
            )

        if dry_run:
            return ShiftImportOutcome(
                imported=False,
                action="dry_run",
                shift_date=target_date,
                shift_type=target_shift,
                updated_fields=selective_updates,
                message="Dry-run extraction succeeded; database was not modified",
            )

        allow_overwrite = (
            overwrite_submitted
            if overwrite_submitted is not None
            else self._settings.OVERWRITE_SUBMITTED_SHIFTS
        )

        async with self._db.connection() as conn:
            store = await self._resolve_store(
                conn,
                organization_id=organization_id,
                store_id_override=store_id_override,
                store_code=extraction.mapped_shift.store_code,
                store_name_hint=extraction.mapped_shift.store_name_hint,
            )

            if not store:
                return ShiftImportOutcome(
                    imported=False,
                    action="skipped_no_store",
                    shift_date=target_date,
                    shift_type=target_shift,
                    updated_fields=selective_updates,
                    message=(
                        f"Could not uniquely resolve RestoOrbit store "
                        f"(code={extraction.mapped_shift.store_code!r}, "
                        f"hint={extraction.mapped_shift.store_name_hint!r}). "
                        "Provide store_id_override or configure single-store mapping."
                    ),
                )

            store_id = str(store["id"])
            org_id = str(store["organization_id"])

            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, status, net_sales, labor_cost, labor_percent, service_metrics, yearly_sales
                    FROM daily_shifts
                    WHERE store_id = %s
                      AND shift_date = %s
                      AND shift_type = %s
                      AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    (store_id, target_date, target_shift),
                )
                existing = await cur.fetchone()

                if existing:
                    if existing["status"] == "Submitted" and not allow_overwrite:
                        return ShiftImportOutcome(
                            imported=False,
                            action="skipped_submitted",
                            daily_shift_id=existing["id"],
                            store_id=store["id"],
                            store_name=store["name"],
                            shift_date=target_date,
                            shift_type=target_shift,
                            updated_fields=selective_updates,
                            message=(
                                f"Shift {target_shift} on {target_date} is already Submitted; "
                                "skipped overwrite (set overwrite_submitted=true to force)."
                            ),
                        )

                    new_net_sales = selective_updates.get(
                        "net_sales",
                        float(existing["net_sales"] or 0.0),
                    )
                    new_labor_cost = selective_updates.get(
                        "labor_cost",
                        float(existing["labor_cost"] or 0.0),
                    )
                    if new_net_sales > 0 and (
                        "net_sales" in selective_updates or "labor_cost" in selective_updates
                    ):
                        new_labor_percent = round((new_labor_cost / new_net_sales) * 100, 2)
                    else:
                        new_labor_percent = selective_updates.get(
                            "labor_percent",
                            float(existing["labor_percent"] or 0.0),
                        )

                    merged_service = {
                        **_DEFAULT_SERVICE_METRICS,
                        **(existing.get("service_metrics") or {}),
                        **(selective_updates.get("service_metrics") or {}),
                    }

                    new_yearly = existing.get("yearly_sales")
                    if "yearly_sales" in selective_updates:
                        y_in = selective_updates["yearly_sales"]
                        new_yearly = {
                            **(new_yearly or {}),
                            **y_in,
                            "variance": round(
                                (y_in.get("thisYear", 0.0) - y_in.get("lastYear", 0.0)), 2
                            ),
                        }

                    await cur.execute(
                        """
                        UPDATE daily_shifts SET
                          status = CASE WHEN status = 'Pending' THEN 'In Progress' ELSE status END,
                          net_sales = %s,
                          labor_cost = %s,
                          labor_percent = %s,
                          trainees_count = COALESCE(%s, trainees_count),
                          pulled_cars = COALESCE(%s, pulled_cars),
                          service_metrics = %s::jsonb,
                          yearly_sales = COALESCE(%s::jsonb, yearly_sales),
                          notes = COALESCE(%s, notes),
                          last_sync_by = 'Auto-Data-Extractor',
                          last_sync_at = CURRENT_TIMESTAMP,
                          updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                        RETURNING id
                        """,
                        (
                            new_net_sales,
                            new_labor_cost,
                            new_labor_percent,
                            selective_updates.get("trainees_count"),
                            selective_updates.get("pulled_cars"),
                            json.dumps(merged_service),
                            json.dumps(new_yearly) if new_yearly is not None else None,
                            selective_updates.get("notes"),
                            str(existing["id"]),
                        ),
                    )
                    updated_row = await cur.fetchone()
                    return ShiftImportOutcome(
                        imported=True,
                        action="updated_existing",
                        daily_shift_id=updated_row["id"],
                        store_id=store["id"],
                        store_name=store["name"],
                        shift_date=target_date,
                        shift_type=target_shift,
                        updated_fields=selective_updates,
                        message=(
                            f"Updated {target_shift} ({target_date}) for store {store['name']} "
                            "with extracted PDF metrics"
                        ),
                    )

                # Create new 'In Progress' shift record with safe defaults + extracted fields
                prior_baseline = await self._get_prior_safe_baseline(
                    conn, store_id, target_date, target_shift
                )
                shift_time = resolve_shift_timing(
                    store.get("shift_schedule"), target_date, target_shift
                )
                net_sales = selective_updates.get("net_sales", 0.0)
                labor_cost = selective_updates.get("labor_cost", 0.0)
                labor_percent = (
                    round((labor_cost / net_sales) * 100, 2)
                    if net_sales > 0
                    else selective_updates.get("labor_percent", 0.0)
                )
                service_metrics = {
                    **_DEFAULT_SERVICE_METRICS,
                    **(selective_updates.get("service_metrics") or {}),
                }
                yearly_sales = None
                if target_shift == "Shift 2" and "yearly_sales" in selective_updates:
                    y_in = selective_updates["yearly_sales"]
                    yearly_sales = {
                        **y_in,
                        "variance": round(
                            (y_in.get("thisYear", 0.0) - y_in.get("lastYear", 0.0)), 2
                        ),
                    }

                await cur.execute(
                    """
                    INSERT INTO daily_shifts (
                      organization_id,
                      store_id,
                      shift_type,
                      shift_date,
                      shift_time,
                      status,
                      last_sync_by,
                      last_sync_at,
                      net_sales,
                      labor_cost,
                      labor_percent,
                      trainees_count,
                      pulled_cars,
                      service_metrics,
                      prior_safe_count,
                      safe_count_variance,
                      yearly_sales,
                      notes
                    ) VALUES (
                      %s, %s, %s, %s, %s, 'In Progress', 'Auto-Data-Extractor', CURRENT_TIMESTAMP,
                      %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s
                    )
                    RETURNING id
                    """,
                    (
                        org_id,
                        store_id,
                        target_shift,
                        target_date,
                        shift_time,
                        net_sales,
                        labor_cost,
                        labor_percent,
                        selective_updates.get("trainees_count", 0),
                        selective_updates.get("pulled_cars", 0),
                        json.dumps(service_metrics),
                        prior_baseline,
                        round(-prior_baseline, 2),
                        json.dumps(yearly_sales) if yearly_sales is not None else None,
                        selective_updates.get("notes"),
                    ),
                )
                inserted = await cur.fetchone()
                return ShiftImportOutcome(
                    imported=True,
                    action="created_draft",
                    daily_shift_id=inserted["id"],
                    store_id=store["id"],
                    store_name=store["name"],
                    shift_date=target_date,
                    shift_type=target_shift,
                    updated_fields=selective_updates,
                    message=(
                        f"Created {target_shift} ({target_date}) draft for store {store['name']} "
                        "from extracted PDF metrics"
                    ),
                )
