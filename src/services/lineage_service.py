from __future__ import annotations

from typing import Any

from src.repository.metadata_repository import MetadataRepository


class LineageService:
    """
    Business/service layer for metadata lineage.

    This service is model-agnostic and source-agnostic.
    It operates only on metadata exposed by MetadataRepository.
    """

    def __init__(
        self,
        repository: MetadataRepository | None = None,
    ):
        self.repository = repository or MetadataRepository()

    def get_measure_lineage(
        self,
        measure_name: str,
    ) -> dict[str, Any]:
        """
        Return complete lineage for a semantic model measure.
        """

        result = self.repository.get_measure_lineage(
            measure_name
        )

        if not result.get("found"):
            return result

        lineage = result.get("lineage", [])

        return {
            "found": True,
            "measure": result.get("measure"),
            "lineage": lineage,
            "lineage_count": len(lineage),
        }

    def get_column_lineage(
        self,
        semantic_table: str,
        semantic_column: str,
    ) -> dict[str, Any]:
        """
        Return physical source lineage for a semantic column.
        """

        return self.repository.get_column_lineage(
            semantic_table,
            semantic_column,
        )