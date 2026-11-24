"""Strategy interface and registry for modular report extractors."""

from abc import ABC, abstractmethod

from app.extractors.pdf_reader import ParsedPdfDocument
from app.models.schemas import ExtractionResult


class BaseReportExtractor(ABC):
    """Abstract base class implemented by every back-office report extractor."""

    report_type: str
    report_title: str

    @abstractmethod
    def can_handle(self, doc: ParsedPdfDocument, filename: str = "") -> float:
        """Return a match confidence score in [0.0, 1.0]. Higher score wins."""

    @abstractmethod
    def extract(
        self,
        doc: ParsedPdfDocument,
        filename: str = "",
        default_shift_type: str | None = None,
    ) -> ExtractionResult:
        """Extract selective fields from the parsed PDF document."""


class ReportExtractorRegistry:
    """Maintains ordered extractors and dispatches to the highest-confidence match."""

    def __init__(self) -> None:
        self._extractors: list[BaseReportExtractor] = []

    def register(self, extractor: BaseReportExtractor) -> None:
        self._extractors.append(extractor)

    def list_supported_types(self) -> list[dict[str, str]]:
        return [
            {"report_type": ext.report_type, "report_title": ext.report_title}
            for ext in self._extractors
        ]

    def resolve(
        self, doc: ParsedPdfDocument, filename: str = ""
    ) -> tuple[BaseReportExtractor | None, float]:
        best_extractor: BaseReportExtractor | None = None
        best_score = 0.0

        for extractor in self._extractors:
            score = extractor.can_handle(doc, filename)
            if score > best_score:
                best_score = score
                best_extractor = extractor

        return best_extractor, best_score
