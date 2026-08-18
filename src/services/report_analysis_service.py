from __future__ import annotations

from typing import Any

from src.repository.metadata_repository import MetadataRepository


class ReportAnalysisService:
    """
    Business/service layer for report analysis.

    This service does not contain SQL.
    """

    def __init__(
        self,
        repository: MetadataRepository | None = None,
    ):
        self.repository = repository or MetadataRepository()

    def list_reports(self) -> list[dict[str, Any]]:
        """
        Return all reports available in the repository.
        """

        return self.repository.list_reports()

    def get_report_metadata(
        self,
        report_name: str,
    ) -> dict[str, Any]:
        """
        Return complete structural metadata for a report.
        """

        return self.repository.get_report_metadata(
            report_name
        )

    def get_report_visuals(
        self,
        report_name: str,
    ) -> list[dict[str, Any]]:
        """
        Return report visuals and their semantic fields.
        """

        return self.repository.get_report_visuals(
            report_name
        )