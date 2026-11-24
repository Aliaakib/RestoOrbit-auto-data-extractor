"""High-speed in-memory PDF text and borderless-table reader."""

import io
from dataclasses import dataclass, field

import pdfplumber
from pypdf import PdfReader

from app.core.exceptions import BadRequestError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ParsedPdfDocument:
    """Normalized representation of a PDF's pages, lines, and extracted tables."""

    page_count: int
    full_text: str
    pages_text: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def _clean_cell(cell: str | None) -> str:
    if not cell:
        return ""
    return " ".join(str(cell).split()).strip()


def read_pdf_document(pdf_bytes: bytes, filename: str = "document.pdf") -> ParsedPdfDocument:
    """Extract text, normalized lines, and tables (bordered & borderless) from PDF bytes."""
    pages_text: list[str] = []
    tables: list[list[list[str]]] = []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)
            if page_count == 0:
                raise BadRequestError(f"PDF '{filename}' contains zero pages")

            for page in pdf.pages:
                text = page.extract_text(layout=True) or page.extract_text() or ""
                pages_text.append(text)

                # First try standard bordered tables, then borderless text-aligned tables
                extracted_tables = page.extract_tables() or []
                if not extracted_tables:
                    extracted_tables = (
                        page.extract_tables(
                            table_settings={
                                "vertical_strategy": "text",
                                "horizontal_strategy": "text",
                                "snap_tolerance": 4,
                                "join_tolerance": 4,
                                "intersection_tolerance": 5,
                            }
                        )
                        or []
                    )

                for raw_table in extracted_tables:
                    cleaned_table = [
                        [_clean_cell(cell) for cell in row]
                        for row in raw_table
                        if row and any(_clean_cell(cell) for cell in row)
                    ]
                    if cleaned_table:
                        tables.append(cleaned_table)
    except BadRequestError:
        raise
    except Exception as exc:
        logger.warning("pdfplumber_failed_trying_pypdf", filename=filename, error=str(exc))
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            page_count = len(reader.pages)
            pages_text = [(p.extract_text() or "") for p in reader.pages]
        except Exception as pypdf_exc:
            raise BadRequestError(
                f"Unable to parse PDF '{filename}': {pypdf_exc}"
            ) from pypdf_exc

    full_text = "\n".join(pages_text).strip()
    if not full_text:
        raise BadRequestError(
            f"PDF '{filename}' contains no extractable text (scanned image PDFs require OCR)"
        )

    lines = [line.strip() for line in full_text.splitlines() if line.strip()]
    return ParsedPdfDocument(
        page_count=page_count,
        full_text=full_text,
        pages_text=pages_text,
        tables=tables,
        lines=lines,
    )
