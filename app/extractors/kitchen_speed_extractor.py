"""Extractor for 'Kitchen Speed of Service by Hour' back-office PDFs."""

import re
from typing import Any

from app.extractors.base import BaseReportExtractor
from app.extractors.pdf_reader import ParsedPdfDocument
from app.models.schemas import ExtractionResult, MappedShiftFields
from app.utils.parsers import extract_store_header, parse_duration, parse_report_date

_DURATION_TOKEN_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_HOUR_RANGE_RE = re.compile(
    r"^(\d{1,2}:\d{2}\s*(?:AM|PM)\s*-\s*\d{1,2}:\d{2}\s*(?:AM|PM))\s+(.*)$",
    re.IGNORECASE,
)
_GENERATED_AT_RE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s*(?:AM|PM)(?:\s*\([^)]+\).*?)?)$",
    re.IGNORECASE | re.MULTILINE,
)

# Canonical column order in Kitchen Speed of Service by Hour report
_CANONICAL_COLUMNS = [
    "dt_chill",
    "dt_hot",
    "dt_expo",
    "fc_hot",
    "fc_chill",
    "fc_dt_fryer",
    "fc_dt_oj",
    "spare_queue",
    "fc_expo",
    "menu_item_header",
    "spare_1",
    "spare_2",
    "spare_3",
    "spare_4",
    "average",
]


def _normalize_col_key(header: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")
    aliases = {
        "dt_chill": "dt_chill",
        "dt_hot": "dt_hot",
        "dt_expo": "dt_expo",
        "fc_hot": "fc_hot",
        "fc_chill": "fc_chill",
        "fc_dt_fryer": "fc_dt_fryer",
        "fc_dt_oj": "fc_dt_oj",
        "spare_queue": "spare_queue",
        "fc_expo": "fc_expo",
        "menu_item_header": "menu_item_header",
        "spare_1": "spare_1",
        "spare_2": "spare_2",
        "spare_3": "spare_3",
        "spare_4": "spare_4",
        "average": "average",
    }
    return aliases.get(clean, clean)


class KitchenSpeedExtractor(BaseReportExtractor):
    """Extracts DT Expo Average and station service times for Shift Service Metrics."""

    report_type = "kitchen_speed_of_service_by_hour"
    report_title = "Kitchen Speed of Service by Hour"

    def can_handle(self, doc: ParsedPdfDocument, filename: str = "") -> float:
        text_lower = doc.full_text.lower()
        score = 0.0
        if "kitchen speed of service by hour" in text_lower:
            score += 0.7
        elif "kitchen speed of service" in text_lower:
            score += 0.45

        if "dt expo" in text_lower:
            score += 0.2
        if "dt chill" in text_lower and "fc expo" in text_lower:
            score += 0.1
        return min(score, 1.0)

    def _extract_from_tables(
        self, doc: ParsedPdfDocument
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        averages: dict[str, str] = {}
        hourly_rows: list[dict[str, Any]] = []

        for table in doc.tables:
            if not table:
                continue

            header_idx = None
            col_indices: dict[str, int] = {}
            for idx, row in enumerate(table):
                joined = " ".join(c.lower() for c in row)
                if "dt expo" in joined and ("dt chill" in joined or "fc hot" in joined):
                    header_idx = idx
                    for col_i, cell in enumerate(row):
                        norm = _normalize_col_key(cell)
                        if norm:
                            col_indices[norm] = col_i
                    break

            if header_idx is None or "dt_expo" not in col_indices:
                continue

            hour_col = col_indices.get("hour", 0)
            for row in table[header_idx + 1 :]:
                label = (row[hour_col] if hour_col < len(row) else "").strip()
                if not label:
                    continue

                if label.lower() == "average":
                    for key, col_i in col_indices.items():
                        if key == "hour" or col_i >= len(row):
                            continue
                        val = row[col_i].strip()
                        if val:
                            averages[key] = val
                elif _HOUR_RANGE_RE.match(label + " x"):
                    row_data: dict[str, Any] = {"hour": label}
                    for key, col_i in col_indices.items():
                        if key == "hour" or col_i >= len(row):
                            continue
                        if row[col_i].strip():
                            row_data[key] = row[col_i].strip()
                    hourly_rows.append(row_data)

        return averages, hourly_rows

    def _extract_from_lines(
        self, doc: ParsedPdfDocument
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        averages: dict[str, str] = {}
        hourly_rows: list[dict[str, Any]] = []

        # Determine dynamic column order if header line is present, else canonical order
        col_order = list(_CANONICAL_COLUMNS)
        for line in doc.lines:
            lower = line.lower()
            if "dt expo" in lower and "dt chill" in lower:
                # Re-verify order from known header tokens in appearance order
                known_headers = [
                    ("DT Chill", "dt_chill"),
                    ("DT Hot", "dt_hot"),
                    ("DT Expo", "dt_expo"),
                    ("FC Hot", "fc_hot"),
                    ("FC Chill", "fc_chill"),
                    ("FC/DT Fryer", "fc_dt_fryer"),
                    ("FC/DT OJ", "fc_dt_oj"),
                    ("Spare Queue", "spare_queue"),
                    ("FC Expo", "fc_expo"),
                    ("Menu Item Header", "menu_item_header"),
                    ("Spare 1", "spare_1"),
                    ("Spare 2", "spare_2"),
                    ("Spare 3", "spare_3"),
                    ("Spare 4", "spare_4"),
                    ("Average", "average"),
                ]
                found = []
                for label, key in known_headers:
                    pos = lower.find(label.lower())
                    if pos != -1:
                        found.append((pos, key))
                if len(found) >= 5:
                    found.sort(key=lambda item: item[0])
                    col_order = [key for _, key in found]
                break

        for line in doc.lines:
            normalized = " ".join(line.split())
            if normalized.lower().startswith("average"):
                tokens = _DURATION_TOKEN_RE.findall(normalized)
                if len(tokens) >= 3:
                    for key, tok in zip(col_order, tokens):
                        averages[key] = tok
                continue

            hour_match = _HOUR_RANGE_RE.match(normalized)
            if hour_match:
                tokens = _DURATION_TOKEN_RE.findall(hour_match.group(2))
                row_data: dict[str, Any] = {"hour": hour_match.group(1)}
                for key, tok in zip(col_order, tokens):
                    row_data[key] = tok
                hourly_rows.append(row_data)

        return averages, hourly_rows

    def extract(
        self,
        doc: ParsedPdfDocument,
        filename: str = "",
        default_shift_type: str | None = None,
    ) -> ExtractionResult:
        store_code, store_name = extract_store_header(doc.full_text)
        report_date = parse_report_date(doc.full_text)
        gen_match = _GENERATED_AT_RE.search(doc.full_text)
        generated_at_raw = gen_match.group(1).strip() if gen_match else None

        line_averages, line_hourly = self._extract_from_lines(doc)
        table_averages, table_hourly = self._extract_from_tables(doc)

        averages = line_averages if "dt_expo" in line_averages else table_averages
        hourly_rows = line_hourly or table_hourly

        warnings: list[str] = []
        dt_expo_raw = averages.get("dt_expo")
        dt_expo_parts = parse_duration(dt_expo_raw)

        if dt_expo_parts is None:
            warnings.append("Could not extract 'DT Expo' value from Average row")

        parsed_averages = {}
        for key, raw_val in averages.items():
            parts = parse_duration(raw_val)
            parsed_averages[key] = parts.model_dump() if parts else raw_val

        shift_type = default_shift_type or "Shift 2"
        confidence = 0.98 if dt_expo_parts is not None else 0.5

        return ExtractionResult(
            report_type=self.report_type,
            report_title=self.report_title,
            confidence=confidence,
            store_code=store_code,
            store_name=store_name,
            report_date=report_date,
            generated_at_raw=generated_at_raw,
            extracted_data={
                "averages": parsed_averages,
                "dt_expo_average": dt_expo_parts.model_dump() if dt_expo_parts else None,
                "hourly_rows": hourly_rows,
            },
            mapped_shift=MappedShiftFields(
                store_code=store_code,
                store_name_hint=store_name,
                shift_date=report_date,
                shift_type=shift_type,  # type: ignore[arg-type]
                shift_minutes=dt_expo_parts.minutes if dt_expo_parts else None,
                shift_seconds=dt_expo_parts.seconds if dt_expo_parts else None,
            ),
            warnings=warnings,
        )
