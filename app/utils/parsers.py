"""Deterministic parsers for currencies, percentages, durations, dates, and store identifiers."""

import re
from datetime import date, datetime

from app.models.schemas import DurationParts

_CURRENCY_RE = re.compile(r"[-+]?\$?\s*([\d,]+(?:\.\d+)?)")
_PERCENT_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*%?")
_STORE_CODE_RE = re.compile(r"\b(\d{4,6})\s*[-–]\s*([A-Za-z0-9 ,.&'-]+)")
_LONG_DATE_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
    r"([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def parse_currency(raw: str | float | int | None) -> float | None:
    """Parse formatted currency like '$3,754.12' or '($12.50)' into a rounded float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return round(float(raw), 2)

    text = str(raw).strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")") or text.startswith("-")
    match = _CURRENCY_RE.search(text.replace("(", "").replace(")", ""))
    if not match:
        return None

    value = float(match.group(1).replace(",", ""))
    return round(-value if negative else value, 2)


def parse_int(raw: str | int | float | None) -> int | None:
    """Parse formatted integer like '1,234' into int."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    text = re.sub(r"[^\d-]", "", str(raw).strip())
    if not text or text == "-":
        return None
    return int(text)


def parse_float(raw: str | float | int | None) -> float | None:
    """Parse numeric string like '61.22' into float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def parse_percent(raw: str | float | int | None) -> float | None:
    """Parse percentage string like '20.53%' into float 20.53."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return round(float(raw), 2)
    match = _PERCENT_RE.search(str(raw).strip())
    return round(float(match.group(1)), 2) if match else None


def parse_duration(raw: str | None) -> DurationParts | None:
    """Parse 'MM:SS' (e.g. '5:39') or 'H:MM:SS' (e.g. '1:02:25') into minutes and seconds.

    Note: In RestoOrbit Service Metrics, values are stored as (minutes, seconds),
    so '1:02:25' converts to 62 minutes, 25 seconds.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    parts = text.split(":")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        mins = int(parts[0])
        secs = int(parts[1])
    elif len(parts) == 3 and all(p.isdigit() for p in parts):
        hours = int(parts[0])
        mins = hours * 60 + int(parts[1])
        secs = int(parts[2])
    else:
        return None

    if secs >= 60:
        mins += secs // 60
        secs = secs % 60

    total_seconds = mins * 60 + secs
    return DurationParts(
        raw=text,
        minutes=mins,
        seconds=secs,
        total_seconds=total_seconds,
    )


def parse_report_date(text: str) -> date | None:
    """Extract report date from long format ('Tuesday, November 24, 2026'), MM/DD/YYYY, or ISO."""
    if not text:
        return None

    long_match = _LONG_DATE_RE.search(text)
    if long_match:
        date_fragment = re.sub(r"\s+", " ", long_match.group(1).strip())
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(date_fragment, fmt).date()
            except ValueError:
                continue

    iso_match = _ISO_DATE_RE.search(text)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            pass

    num_match = _NUMERIC_DATE_RE.search(text)
    if num_match:
        month, day, year = int(num_match.group(1)), int(num_match.group(2)), int(num_match.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            pass

    return None


def extract_store_header(text: str) -> tuple[str | None, str | None]:
    """Extract (store_code, store_name) from strings like '10912 - Ellijay, GA - GC-B'."""
    if not text:
        return None, None

    for line in text.splitlines()[:12]:
        clean = " ".join(line.split())
        match = _STORE_CODE_RE.search(clean)
        if match:
            code = match.group(1).strip()
            name = f"{code} - {match.group(2).strip()}"
            return code, name

    if "all dq locations" in text.lower():
        return None, "All DQ Locations"

    return None, None
