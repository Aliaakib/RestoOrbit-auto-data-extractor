"""Unit tests for ShiftImporter selective field mapping and shift type inference."""

from datetime import date

from app.models.schemas import ExtractionResult, MappedShiftFields
from app.services.shift_importer import ShiftImporter, infer_shift_type, resolve_shift_timing


def test_infer_shift_type_priority():
    assert infer_shift_type("Shift 1", "Shift 2 Report", "Shift 2", "Shift 2") == "Shift 1"
    assert infer_shift_type(None, "Daily Report - Shift 1 Close", "Shift 2", "Shift 2") == "Shift 1"
    assert infer_shift_type(None, "Daily Report", "Shift 1", "Shift 2") == "Shift 1"
    assert infer_shift_type(None, "Daily Report", None, "Shift 2") == "Shift 2"


def test_build_selective_updates_only_non_null_fields():
    sales_extraction = ExtractionResult(
        report_type="sales_and_labor_by_hour",
        report_title="Sales and Labor Report by Hour",
        confidence=0.98,
        report_date=date(2026, 11, 24),
        mapped_shift=MappedShiftFields(
            shift_date=date(2026, 11, 24),
            net_sales=3754.12,
            labor_cost=770.67,
            labor_percent=20.53,
        ),
    )
    sales_updates = ShiftImporter.build_selective_updates(sales_extraction)
    assert sales_updates == {
        "net_sales": 3754.12,
        "labor_cost": 770.67,
        "labor_percent": 20.53,
    }
    assert "service_metrics" not in sales_updates

    speed_extraction = ExtractionResult(
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
    speed_updates = ShiftImporter.build_selective_updates(speed_extraction)
    assert speed_updates == {
        "service_metrics": {
            "shiftMinutes": 5,
            "shiftSeconds": 39,
        }
    }


def test_resolve_shift_timing_from_store_schedule():
    schedule = {
        "tuesday": {
            "shift1": {"from": "7:00 AM", "to": "3:00 PM"},
            "shift2": {"from": "3:00 PM", "to": "11:00 PM"},
        }
    }
    assert resolve_shift_timing(schedule, date(2026, 11, 24), "Shift 2") == "3:00 PM – 11:00 PM"
    assert resolve_shift_timing(None, date(2026, 11, 24), "Shift 2") == "4:00 PM – 12:00 AM"
