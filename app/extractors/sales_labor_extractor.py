"""Extractor for 'Sales and Labor Report by Hour' back-office PDFs."""

import re
from typing import Any

from app.extractors.base import BaseReportExtractor
from app.extractors.pdf_reader import ParsedPdfDocument
from app.models.schemas import ExtractionResult, MappedShiftFields
from app.utils.parsers import (
    extract_store_header,
    parse_currency,
    parse_float,
    parse_int,
    parse_percent,
    parse_report_date,
)

# Matches rows like:
# Total   $3,754.12   301   $12.47   301   $12.47   61.22   $770.67   20.53%
_TOTAL_LINE_RE = re.compile(
    r"^Total\s+"
    r"(\$?[\d,]+\.\d{2})\s+"
    r"([\d,]+)\s+"
    r"(\$?[\d,]+\.\d{2})\s+"
    r"([\d,]+)\s+"
    r"(\$?[\d,]+\.\d{2})\s+"
    r"([\d,]+\.\d{2})\s+"
    r"(\$?[\d,]+\.\d{2})\s+"
    r"([\d.]+%?)",
    re.IGNORECASE,
)

# Matches hourly rows like:
# 10:00 AM   $131.27   9   $14.59   9   $14.59   7.25   $102.72   78.25%
_HOURLY_LINE_RE = re.compile(
    r"^(\d{1,2}:\d{2}\s*(?:AM|PM))\s+"
    r"(\$?[\d,]+\.\d{2})\s+"
    r"([\d,]+)\s+"
    r"(\$?[\d,]+\.\d{2})\s+"
    r"([\d,]+)\s+"
    r"(\$?[\d,]+\.\d{2})\s+"
    r"([\d,]+\.\d{2})\s+"
    r"(\$?[\d,]+\.\d{2})\s+"
    r"([\d.]+%?)",
    re.IGNORECASE,
)

_GENERATED_AT_RE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s*(?:AM|PM)(?:\s*\([^)]+\).*?)?)$",
    re.IGNORECASE | re.MULTILINE,
)


class SalesAndLaborExtractor(BaseReportExtractor):
    """Extracts total Net Sales, Labor Cost, Labor %, and hourly breakdown."""

    report_type = "sales_and_labor_by_hour"
    report_title = "Sales and Labor Report by Hour"

    def can_handle(self, doc: ParsedPdfDocument, filename: str = "") -> float:
        text_lower = doc.full_text.lower()
        score = 0.0
        if "sales and labor report by hour" in text_lower:
            score += 0.7
        elif "sales and labor" in text_lower:
            score += 0.45

        if "net sales" in text_lower and "labor dollars" in text_lower:
            score += 0.25
        if "labor percent" in text_lower:
            score += 0.05
        return min(score, 1.0)

    def _extract_from_tables(self, doc: ParsedPdfDocument) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        totals: dict[str, Any] = {}
        hourly_rows: list[dict[str, Any]] = []

        for table in doc.tables:
            if not table:
                continue

            header_idx = None
            col_map: dict[str, int] = {}
            for idx, row in enumerate(table):
                joined = " ".join(c.lower() for c in row)
                if "net sales" in joined and ("labor" in joined or "guests" in joined):
                    header_idx = idx
                    for col_i, cell in enumerate(row):
                        key = cell.lower().strip()
                        if "hour" == key or key.startswith("hour"):
                            col_map["hour"] = col_i
                        elif "net sales" in key:
                            col_map["net_sales"] = col_i
                        elif key == "guests":
                            col_map["guests"] = col_i
                        elif "guest average" in key:
                            col_map["guest_average"] = col_i
                        elif key == "orders":
                            col_map["orders"] = col_i
                        elif "order average" in key:
                            col_map["order_average"] = col_i
                        elif "labor hours" in key:
                            col_map["labor_hours"] = col_i
                        elif "labor dollars" in key or "labor cost" in key:
                            col_map["labor_cost"] = col_i
                        elif "labor percent" in key:
                            col_map["labor_percent"] = col_i
                    break

            if header_idx is None or "net_sales" not in col_map:
                continue

            def _cell(row: list[str], field: str) -> str | None:
                pos = col_map.get(field)
                if pos is None or pos >= len(row):
                    return None
                return row[pos]

            for row in table[header_idx + 1 :]:
                label = (row[col_map.get("hour", 0)] if row else "").strip()
                if not label:
                    continue

                if label.lower() == "total":
                    totals = {
                        "net_sales": parse_currency(_cell(row, "net_sales")),
                        "guests": parse_int(_cell(row, "guests")),
                        "guest_average": parse_currency(_cell(row, "guest_average")),
                        "orders": parse_int(_cell(row, "orders")),
                        "order_average": parse_currency(_cell(row, "order_average")),
                        "labor_hours": parse_float(_cell(row, "labor_hours")),
                        "labor_cost": parse_currency(_cell(row, "labor_cost")),
                        "labor_percent": parse_percent(_cell(row, "labor_percent")),
                    }
                elif re.match(r"^\d{1,2}:\d{2}\s*(?:AM|PM)$", label, re.IGNORECASE):
                    hourly_rows.append(
                        {
                            "hour": label,
                            "net_sales": parse_currency(_cell(row, "net_sales")),
                            "guests": parse_int(_cell(row, "guests")),
                            "orders": parse_int(_cell(row, "orders")),
                            "labor_hours": parse_float(_cell(row, "labor_hours")),
                            "labor_cost": parse_currency(_cell(row, "labor_cost")),
                            "labor_percent": parse_percent(_cell(row, "labor_percent")),
                        }
                    )

        return totals, hourly_rows

    def _extract_from_lines(self, doc: ParsedPdfDocument) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        totals: dict[str, Any] = {}
        hourly_rows: list[dict[str, Any]] = []

        for line in doc.lines:
            normalized = " ".join(line.split())
            total_match = _TOTAL_LINE_RE.match(normalized)
            if total_match:
                totals = {
                    "net_sales": parse_currency(total_match.group(1)),
                    "guests": parse_int(total_match.group(2)),
                    "guest_average": parse_currency(total_match.group(3)),
                    "orders": parse_int(total_match.group(4)),
                    "order_average": parse_currency(total_match.group(5)),
                    "labor_hours": parse_float(total_match.group(6)),
                    "labor_cost": parse_currency(total_match.group(7)),
                    "labor_percent": parse_percent(total_match.group(8)),
                }
                continue

            hour_match = _HOURLY_LINE_RE.match(normalized)
            if hour_match:
                hourly_rows.append(
                    {
                        "hour": hour_match.group(1),
                        "net_sales": parse_currency(hour_match.group(2)),
                        "guests": parse_int(hour_match.group(3)),
                        "orders": parse_int(hour_match.group(5)),
                        "labor_hours": parse_float(hour_match.group(7)),
                        "labor_cost": parse_currency(hour_match.group(8)),
                        "labor_percent": parse_percent(hour_match.group(9)),
                    }
                )

        return totals, hourly_rows

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

        # Prefer layout line regex first (fast & immune to borderless column split quirks),
        # then merge/fallback with pdfplumber table extraction.
        line_totals, line_hourly = self._extract_from_lines(doc)
        table_totals, table_hourly = self._extract_from_tables(doc)

        totals = line_totals if line_totals.get("net_sales") is not None else table_totals
        hourly_rows = line_hourly or table_hourly
        warnings: list[str] = []

        net_sales = totals.get("net_sales")
        labor_cost = totals.get("labor_cost")
        labor_percent = totals.get("labor_percent")

        if net_sales is None:
            warnings.append("Could not locate 'Total' Net Sales in Sales and Labor report")
        if labor_cost is None:
            warnings.append("Could not locate 'Total' Labor Dollars in Sales and Labor report")

        if labor_percent is None and net_sales and labor_cost is not None and net_sales > 0:
            labor_percent = round((labor_cost / net_sales) * 100, 2)

        if store_code is None and store_name == "All DQ Locations":
            warnings.append(
                "Report header specifies 'All DQ Locations' without a specific 5-digit store code"
            )

        confidence = 0.98 if (net_sales is not None and labor_cost is not None) else 0.55

        return ExtractionResult(
            report_type=self.report_type,
            report_title=self.report_title,
            confidence=confidence,
            store_code=store_code,
            store_name=store_name,
            report_date=report_date,
            generated_at_raw=generated_at_raw,
            extracted_data={
                "totals": totals,
                "hourly_rows": hourly_rows,
            },
            mapped_shift=MappedShiftFields(
                store_code=store_code,
                store_name_hint=store_name,
                shift_date=report_date,
                shift_type=default_shift_type,  # type: ignore[arg-type]
                net_sales=net_sales,
                labor_cost=labor_cost,
                labor_percent=labor_percent,
            ),
            warnings=warnings,
        )
