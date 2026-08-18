from __future__ import annotations

from typing import Any

from src.repository.metadata_repository import MetadataRepository


class ImpactAnalysisService:
    """
    Business/service layer for metadata impact analysis.

    Model-agnostic and report-agnostic.
    """

    def __init__(
        self,
        repository: MetadataRepository | None = None,
    ):
        self.repository = repository or MetadataRepository()

    def find_reports_using_measure(
        self,
        measure_name: str,
    ) -> dict[str, Any]:
        """
        Find reports, pages and visuals using a measure.
        """

        return self.repository.find_reports_using_measure(
            measure_name
        )

    def find_reports_using_column(
        self,
        semantic_table: str,
        semantic_column: str,
    ) -> list[dict[str, Any]]:
        """
        Find report visuals using a semantic column.
        """

        return self.repository.find_reports_using_column(
            semantic_table,
            semantic_column,
        )

    def find_unused_measures(
        self,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Find measures that are not used by reports.

        If model_name is supplied, restrict the analysis
        to that semantic model.
        """

        return self.repository.find_unused_measures(
            model_name
        )