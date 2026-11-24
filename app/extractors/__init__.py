"""Modular PDF report extractors package."""

from app.extractors.base import BaseReportExtractor, ReportExtractorRegistry
from app.extractors.generic_rule_extractor import GenericRuleExtractor
from app.extractors.kitchen_speed_extractor import KitchenSpeedExtractor
from app.extractors.sales_labor_extractor import SalesAndLaborExtractor


def build_default_registry() -> ReportExtractorRegistry:
    """Create and populate the default extractor registry."""
    registry = ReportExtractorRegistry()
    registry.register(SalesAndLaborExtractor())
    registry.register(KitchenSpeedExtractor())
    return registry


__all__ = [
    "BaseReportExtractor",
    "ReportExtractorRegistry",
    "SalesAndLaborExtractor",
    "KitchenSpeedExtractor",
    "GenericRuleExtractor",
    "build_default_registry",
]
