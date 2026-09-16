"""
Metadata Intelligence Platform

MCP server for Microsoft Fabric MetadataRepository.

Architecture:

    VS Code Copilot / Copilot Studio Agent
              |
              v
    MCP Metadata Server
              |
              +----------------------+
              |                      |
              v                      v
    Services Layer             Extraction Service
    (read-only tools)          (extraction tools)
              |                      |
              v                      v
       MetadataRepository      Extractor Scripts
                                      |
                                      v
                              MetadataRepository
                                      |
                                      v
                              Fabric Warehouse

IMPORTANT:

    The original tools (1-11) are READ-ONLY.

    Database queries are implemented in:

        src.repository.metadata_repository

    Business/service logic is implemented in:

        src.services.lineage_service
        src.services.impact_analysis_service
        src.services.report_analysis_service

    Extraction orchestration is implemented in:

        src.services.extraction_service

    Tools 12+ are NOT read-only. They trigger the extraction
    pipeline in C:\\Projects\\MetadataPlatform\\extractors, which
    WRITES to MetadataRepository.

    The extractor scripts themselves remain in:

        C:\\Projects\\MetadataPlatform\\extractors

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
from src.services.extraction_service import ExtractionService


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

logger = logging.getLogger("metadata-mcp")


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

extraction_service = ExtractionService()


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
# TOOL 12 - START SQL SOURCE EXTRACTION
# ============================================================================

@mcp.tool()
def start_sql_extraction() -> dict[str, Any]:
    """
    Start the physical SQL source metadata extraction pipeline.

    This WRITES to MetadataRepository and populates physical
    SQL source metadata such as databases, tables, and columns.

    The extraction runs in the background.

    A browser window may open on this machine for Microsoft Entra
    sign-in. Complete the sign-in to allow the extraction to proceed.

    Returns a job_id. Use get_extraction_status with that job_id
    to check progress.

    This should normally be run BEFORE semantic model extraction
    because semantic source-lineage resolution depends on the
    physical metadata being present.
    """
    return extraction_service.start_sql_extraction()


# ============================================================================
# TOOL 13 - START SEMANTIC MODEL EXTRACTION
# ============================================================================

@mcp.tool()
def start_semantic_model_extraction() -> dict[str, Any]:
    """
    Start the Fabric semantic model metadata extraction pipeline.

    This WRITES to MetadataRepository and populates semantic models,
    tables, columns, measures, relationships, and source lineage.

    The extraction runs in the background.

    A browser window may open on this machine for Microsoft Entra
    sign-in. Complete the sign-in to allow the extraction to proceed.

    Returns a job_id. Use get_extraction_status with that job_id
    to check progress.

    Run SQL source extraction first if physical source metadata
    may be missing or stale.

    This should normally be completed BEFORE report extraction.
    """
    return extraction_service.start_semantic_model_extraction()


# ============================================================================
# TOOL 14 - START REPORT EXTRACTION
# ============================================================================

@mcp.tool()
def start_report_extraction() -> dict[str, Any]:
    """
    Start the Fabric report metadata extraction pipeline.

    This WRITES to MetadataRepository and populates reports, pages,
    visuals, visual fields, visual filters, and report lineage.

    The extraction runs in the background.

    A browser window may open on this machine for Microsoft Entra
    sign-in. Complete the sign-in to allow the extraction to proceed.

    Returns a job_id. Use get_extraction_status with that job_id
    to check progress.

    Semantic model extraction should normally be completed before
    starting report extraction because report fields are resolved
    against the semantic models stored in MetadataRepository.
    """
    return extraction_service.start_report_extraction()


# ============================================================================
# TOOL 15 - EXTRACTION JOB STATUS
# ============================================================================

@mcp.tool()
def get_extraction_status(
    job_id: str,
) -> dict[str, Any]:
    """
    Check the status of a previously started extraction job.

    Status values:
        - starting
        - running
        - succeeded
        - failed

    Returns the job metadata and the latest portion of the
    extraction log output.
    """
    return extraction_service.get_extraction_status(
        job_id
    )


# ============================================================================
# TOOL 16 - LIST EXTRACTION JOBS
# ============================================================================

@mcp.tool()
def list_extraction_jobs() -> list[dict[str, Any]]:
    """
    List all extraction jobs started in this server session,
    most recent first.
    """
    return extraction_service.list_extraction_jobs()


# ============================================================================
# TOOL 17 - RUN FULL METADATA REFRESH
# ============================================================================

@mcp.tool()
def run_full_metadata_refresh() -> dict[str, Any]:
    """
    Run the complete metadata extraction pipeline in the correct order:

        1. SQL source extraction
        2. Semantic model extraction
        3. Report extraction

    Each step waits for the previous step to finish before the
    next step starts.

    A browser window may open for Microsoft Entra sign-in during
    the extraction steps. Complete each sign-in when requested.

    This call blocks until all three steps finish or one fails.

    Returns a summary containing the job_id and final status
    for each step.
    """
    return extraction_service.run_full_metadata_refresh()


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
        "  - ExtractionService"
    )

    logger.info(
        "Extraction scripts directory: %s",
        extraction_service.scripts_dir,
    )

    logger.info(
        "Available MCP tools:"
    )

    logger.info(
        "  [READ-ONLY]"
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

    logger.info(
        "  [WRITES TO METADATAREPOSITORY]"
    )

    logger.info(
        "  - start_sql_extraction"
    )

    logger.info(
        "  - start_semantic_model_extraction"
    )

    logger.info(
        "  - start_report_extraction"
    )

    logger.info(
        "  - get_extraction_status"
    )

    logger.info(
        "  - list_extraction_jobs"
    )

    logger.info(
        "  - run_full_metadata_refresh"
    )

    mcp.run(
        transport="streamable-http"
    )