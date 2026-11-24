"""Dynamic rule-driven extractor for custom back-office PDF report templates."""

import re
from typing import Any

from app.extractors.pdf_reader import ParsedPdfDocument
from app.models.schemas import ExtractionResult, FieldMappingRule, MappedShiftFields
from app.utils.parsers import (
    extract_store_header,
    parse_currency,
    parse_duration,
    parse_float,
    parse_int,
    parse_percent,
    parse_report_date,
)


def _convert_value(raw: str | None, value_type: str) -> Any:
    if raw is None:
        return None
    if value_type == "currency":
        return parse_currency(raw)
    if value_type == "float":
        return parse_float(raw)
    if value_type == "int":
        return parse_int(raw)
    if value_type == "percent":
        return parse_percent(raw)
    if value_type == "duration":
        return parse_duration(raw)
    if value_type == "date":
        return parse_report_date(raw)
    return raw.strip()


def _lookup_table_cell(
    doc: ParsedPdfDocument,
    row_label: str,
    column_header: str,
) -> str | None:
    row_needle = row_label.strip().lower()
    col_needle = column_header.strip().lower()

    for table in doc.tables:
        col_idx: int | None = None
        for r_idx, row in enumerate(table):
            if col_idx is None:
                for c_idx, cell in enumerate(row):
                    if col_needle in cell.lower():
                        col_idx = c_idx
                        break
                continue

            if row and row[0].strip().lower().startswith(row_needle) and col_idx < len(row):
                return row[col_idx].strip()
    return None


class GenericRuleExtractor:
    """Evaluates database-configured FieldMappingRule definitions against a PDF."""

    def extract_with_rule(
        self,
        doc: ParsedPdfDocument,
        rule: dict[str, Any],
        default_shift_type: str | None = None,
    ) -> ExtractionResult:
        store_code, store_name = extract_store_header(doc.full_text)
        report_date = parse_report_date(doc.full_text)

        mapped_kwargs: dict[str, Any] = {
            "store_code": store_code,
            "store_name_hint": store_name,
            "shift_date": report_date,
            "shift_type": default_shift_type or rule.get("default_shift_type"),
        }
        extracted_fields: dict[str, Any] = {}
        warnings: list[str] = []

        raw_mappings = rule.get("field_mappings") or []
        for item in raw_mappings:
            mapping = FieldMappingRule.model_validate(item)
            raw_val: str | None = None

            if mapping.strategy == "regex" and mapping.pattern:
                match = re.search(mapping.pattern, doc.full_text, re.IGNORECASE | re.MULTILINE)
                if match:
                    raw_val = match.group(1) if match.groups() else match.group(0)
            elif mapping.strategy in ("table_cell", "duration_table_cell"):
                if mapping.row_label and mapping.column_header:
                    raw_val = _lookup_table_cell(doc, mapping.row_label, mapping.column_header)

            converted = _convert_value(raw_val, mapping.value_type)
            if converted is None:
                warnings.append(
                    f"Rule '{rule['report_key']}' did not match field '{mapping.target_field}'"
                )
                continue

            if mapping.value_type == "duration" and hasattr(converted, "minutes"):
                extracted_fields[mapping.target_field] = converted.model_dump()
                if mapping.target_field in ("shift_minutes", "shift_seconds"):
                    mapped_kwargs["shift_minutes"] = converted.minutes
                    mapped_kwargs["shift_seconds"] = converted.seconds
                elif mapping.target_field in ("peak_minutes", "peak_seconds"):
                    mapped_kwargs["peak_minutes"] = converted.minutes
                    mapped_kwargs["peak_seconds"] = converted.seconds
            else:
                extracted_fields[mapping.target_field] = converted
                mapped_kwargs[mapping.target_field] = converted

        return ExtractionResult(
            report_type=rule["report_key"],
            report_title=rule["name"],
            confidence=0.9 if extracted_fields else 0.3,
            store_code=mapped_kwargs.get("store_code"),
            store_name=store_name,
            report_date=mapped_kwargs.get("shift_date"),
            extracted_data={"rule_fields": extracted_fields},
            mapped_shift=MappedShiftFields(**mapped_kwargs),
            warnings=warnings,
        )
