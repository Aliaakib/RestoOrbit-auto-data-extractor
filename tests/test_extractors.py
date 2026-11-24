"""Tests verifying exact PDF extraction for back-office reports."""

from datetime import date

from app.extractors import build_default_registry
from app.extractors.pdf_reader import read_pdf_document
from tests.fixtures.pdf_factory import build_kitchen_speed_pdf, build_sales_and_labor_pdf


def test_sales_and_labor_pdf_extraction():
    pdf_bytes = build_sales_and_labor_pdf()
    doc = read_pdf_document(pdf_bytes, filename="sales_labor.pdf")
    registry = build_default_registry()

    extractor, score = registry.resolve(doc, "sales_labor.pdf")
    assert extractor is not None
    assert extractor.report_type == "sales_and_labor_by_hour"
    assert score >= 0.7

    result = extractor.extract(doc, filename="sales_labor.pdf", default_shift_type="Shift 2")
    assert result.report_date == date(2026, 11, 24)
    assert result.store_name == "All DQ Locations"
    assert result.mapped_shift.net_sales == 3754.12
    assert result.mapped_shift.labor_cost == 770.67
    assert result.mapped_shift.labor_percent == 20.53
    assert result.extracted_data["totals"]["guests"] == 301
    assert result.extracted_data["totals"]["orders"] == 301
    assert result.extracted_data["totals"]["labor_hours"] == 61.22
    assert len(result.extracted_data["hourly_rows"]) == 6


def test_kitchen_speed_pdf_extraction():
    pdf_bytes = build_kitchen_speed_pdf()
    doc = read_pdf_document(pdf_bytes, filename="kitchen_speed.pdf")
    registry = build_default_registry()

    extractor, score = registry.resolve(doc, "kitchen_speed.pdf")
    assert extractor is not None
    assert extractor.report_type == "kitchen_speed_of_service_by_hour"
    assert score >= 0.7

    result = extractor.extract(doc, filename="kitchen_speed.pdf", default_shift_type="Shift 2")
    assert result.report_date == date(2026, 11, 24)
    assert result.store_code == "10912"
    assert result.mapped_shift.shift_type == "Shift 2"
    assert result.mapped_shift.shift_minutes == 5
    assert result.mapped_shift.shift_seconds == 39
    assert result.extracted_data["dt_expo_average"]["minutes"] == 5
    assert result.extracted_data["dt_expo_average"]["seconds"] == 39
    assert len(result.extracted_data["hourly_rows"]) == 4
    assert result.extracted_data["hourly_rows"][0]["fc_expo"] == "14:07"
    assert result.extracted_data["hourly_rows"][0]["average"] == "34:03"
