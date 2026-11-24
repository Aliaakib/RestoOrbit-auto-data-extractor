"""Unit tests for deterministic parsers."""

from datetime import date

from app.utils.parsers import (
    extract_store_header,
    parse_currency,
    parse_duration,
    parse_int,
    parse_percent,
    parse_report_date,
)


def test_parse_currency_handles_commas_and_negatives():
    assert parse_currency("$3,754.12") == 3754.12
    assert parse_currency("$770.67") == 770.67
    assert parse_currency("($12.50)") == -12.50
    assert parse_currency(None) is None


def test_parse_duration_handles_mm_ss_and_h_mm_ss():
    short = parse_duration("5:39")
    assert short is not None
    assert short.minutes == 5
    assert short.seconds == 39
    assert short.total_seconds == 339

    long_dur = parse_duration("1:02:25")
    assert long_dur is not None
    assert long_dur.minutes == 62
    assert long_dur.seconds == 25
    assert long_dur.total_seconds == 3745


def test_parse_report_date_long_and_numeric():
    assert parse_report_date("Tuesday, November 24, 2026") == date(2026, 11, 24)
    assert parse_report_date("11/24/2026 2:15:04 PM") == date(2026, 11, 24)
    assert parse_report_date("2026-11-24") == date(2026, 11, 24)


def test_extract_store_header():
    code, name = extract_store_header("Kitchen Speed of Service by Hour\n10912 - Ellijay, GA - GC-B")
    assert code == "10912"
    assert name == "10912 - Ellijay, GA - GC-B"

    code_all, name_all = extract_store_header("Sales and Labor Report by Hour\nAll DQ Locations")
    assert code_all is None
    assert name_all == "All DQ Locations"


def test_parse_int_and_percent():
    assert parse_int("1,301") == 1301
    assert parse_percent("20.53%") == 20.53
