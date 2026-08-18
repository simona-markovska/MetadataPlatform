"""
Metadata Intelligence Platform
Read-only MCP server for Microsoft Fabric MetadataRepository.

Architecture:

    VS Code Copilot
          |
          v
    MCP Metadata Server
          |
          v
    Services Layer
          |
          v
    MetadataRepository
          |
          v
    Fabric Warehouse

IMPORTANT:
    This server is READ-ONLY.

    Database queries are implemented in:
        src.repository.metadata_repository

    Business/service logic is implemented in:
        src.services.lineage_service
        src.services.impact_analysis_service
        src.services.report_analysis_service

    This file contains MCP exposure only.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any


# ============================================================================
# PROJECT PATH
# ============================================================================

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# ============================================================================
# REPOSITORY
# ============================================================================

from src.repository.metadata_repository import MetadataRepository


# ============================================================================
# SERVICES
# ============================================================================

from src.services.lineage_service import LineageService
from src.services.impact_analysis_service import ImpactAnalysisService
from src.services.report_analysis_service import ReportAnalysisService


# ============================================================================
# MCP
# ============================================================================

try:
    from mcp.server import MCPServer

except ImportError as exc:

    raise RuntimeError(
        "The 'mcp' Python package is not installed. "
        "Run: pip install mcp"
    ) from exc


# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(
    "metadata-mcp"
)


# ============================================================================
# MCP SERVER
# ============================================================================

mcp = MCPServer(
    "Metadata Intelligence Platform"
)


# ============================================================================
# REPOSITORY
# ============================================================================

repository = MetadataRepository()


# ============================================================================
# SERVICES
# ============================================================================

lineage_service = LineageService(
    repository
)

impact_service = ImpactAnalysisService(
    repository
)

report_service = ReportAnalysisService(
    repository
)


# ============================================================================
# TOOL 1 - REPOSITORY SUMMARY
# ============================================================================

@mcp.tool()
def get_repository_summary() -> dict[str, Any]:
    """
    Return a high-level summary of the MetadataRepository.

    Useful as the first question when an AI agent connects.
    """

    return repository.get_repository_summary()


# ============================================================================
# TOOL 2 - LIST REPORTS
# ============================================================================

@mcp.tool()
def list_reports() -> list[dict[str, Any]]:
    """
    List all Power BI / Fabric reports stored in MetadataRepository.

    Returns report identity and connected semantic model information.
    """

    return report_service.list_reports()


# ============================================================================
# TOOL 3 - GET REPORT METADATA
# ============================================================================

@mcp.tool()
def get_report_metadata(
    report_name: str,
) -> dict[str, Any]:
    """
    Get complete structural metadata for a report.

    Includes:

        - report
        - pages
        - visuals
        - visual fields
        - visual filters
    """

    return report_service.get_report_metadata(
        report_name
    )


# ============================================================================
# TOOL 4 - GET REPORT VISUALS
# ============================================================================

@mcp.tool()
def get_report_visuals(
    report_name: str,
) -> list[dict[str, Any]]:
    """
    Return all visuals in a report together with their fields.

    Useful for understanding how the semantic model
    is being used by the report.
    """

    return report_service.get_report_visuals(
        report_name
    )


# ============================================================================
# TOOL 5 - GET SEMANTIC MODEL
# ============================================================================

@mcp.tool()
def get_semantic_model(
    model_name: str,
) -> dict[str, Any]:
    """
    Return semantic model structure.

    Includes:

        - semantic model
        - semantic tables
        - semantic columns
        - measures
        - relationships
    """

    return repository.get_semantic_model(
        model_name
    )


# ============================================================================
# TOOL 6 - GET MEASURE
# ============================================================================

@mcp.tool()
def get_measure(
    measure_name: str,
) -> dict[str, Any]:
    """
    Return a measure, its DAX expression,
    semantic model, semantic table,
    and dependency metadata.
    """

    return repository.get_measure(
        measure_name
    )


# ============================================================================
# TOOL 7 - GET MEASURE LINEAGE
# ============================================================================

@mcp.tool()
def get_measure_lineage(
    measure_name: str,
) -> dict[str, Any]:
    """
    Trace a semantic model measure to its physical SQL source.

    Lineage:

        Measure
            ->
        Semantic Column
            ->
        Physical SQL Column
            ->
        Physical SQL Table
            ->
        Physical Database
    """

    return lineage_service.get_measure_lineage(
        measure_name
    )


# ============================================================================
# TOOL 8 - GET COLUMN LINEAGE
# ============================================================================

@mcp.tool()
def get_column_lineage(
    semantic_table: str,
    semantic_column: str,
) -> dict[str, Any]:
    """
    Trace a semantic model column to its physical SQL source column.
    """

    return lineage_service.get_column_lineage(
        semantic_table,
        semantic_column,
    )


# ============================================================================
# TOOL 9 - FIND REPORTS USING MEASURE
# ============================================================================

@mcp.tool()
def find_reports_using_measure(
    measure_name: str,
) -> dict[str, Any]:
    """
    Find every report visual and visual filter
    that uses a specific measure.

    Checks:

        1. MetadataReportVisualField
        2. MetadataReportVisualFilter
    """

    return impact_service.find_reports_using_measure(
        measure_name
    )


# ============================================================================
# TOOL 10 - FIND REPORTS USING COLUMN
# ============================================================================

@mcp.tool()
def find_reports_using_column(
    semantic_table: str,
    semantic_column: str,
) -> list[dict[str, Any]]:
    """
    Find report visuals that directly use a semantic column.
    """

    return impact_service.find_reports_using_column(
        semantic_table,
        semantic_column,
    )


# ============================================================================
# TOOL 11 - FIND UNUSED MEASURES
# ============================================================================

@mcp.tool()
def find_unused_measures(
    model_name: str | None = None,
) -> dict[str, Any]:
    """
    Find measures that are not used by any report visual
    or visual-level filter.

    If model_name is supplied, only that semantic model
    is checked.
    """

    return impact_service.find_unused_measures(
        model_name
    )


# ============================================================================
# SERVER START
# ============================================================================

if __name__ == "__main__":

    logger.info(
        "Starting Metadata Intelligence Platform MCP server..."
    )

    logger.info(
        "Repository: MetadataRepository"
    )

    logger.info(
        "Services:"
    )

    logger.info(
        "  - LineageService"
    )

    logger.info(
        "  - ImpactAnalysisService"
    )

    logger.info(
        "  - ReportAnalysisService"
    )

    logger.info(
        "Available MCP tools:"
    )

    logger.info(
        "  - get_repository_summary"
    )

    logger.info(
        "  - list_reports"
    )

    logger.info(
        "  - get_report_metadata"
    )

    logger.info(
        "  - get_report_visuals"
    )

    logger.info(
        "  - get_semantic_model"
    )

    logger.info(
        "  - get_measure"
    )

    logger.info(
        "  - get_measure_lineage"
    )

    logger.info(
        "  - get_column_lineage"
    )

    logger.info(
        "  - find_reports_using_measure"
    )

    logger.info(
        "  - find_reports_using_column"
    )

    logger.info(
        "  - find_unused_measures"
    )

    mcp.run()