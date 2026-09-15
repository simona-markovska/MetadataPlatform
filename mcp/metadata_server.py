"""
Metadata Intelligence Platform
MCP server for Microsoft Fabric MetadataRepository.

Architecture:

    VS Code Copilot / Copilot Studio Agent
          |
          v
    MCP Metadata Server
          |
          v
    Services Layer  (read-only tools)
    Extractor Scripts (extraction-trigger tools)
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

    Tools 12+ are NOT read-only. They trigger the extraction
    pipeline in C:\\Projects\\MetadataPlatform\\extractors, which
    WRITES to MetadataRepository (the production database -- not the
    V6 folder, which is test-only and not used by this server).

    This file contains MCP exposure only.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
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
# EXTRACTION SCRIPTS
# ============================================================================
#
# IMPORTANT: the tools in this section are NOT read-only. They
# trigger the production extraction pipeline, which writes to
# MetadataRepository.
#
# RUN ORDER MATTERS:
#
#   1. sql_extractor.py
#        Populates MetadataDatabase / MetadataTable / MetadataColumn
#        (physical SQL source metadata).
#
#   2. fabric_semantic_model_extractor.py
#        Populates MetadataSemanticModel / -Table / -Column /
#        Measure / Relationship, and resolves source lineage against
#        the physical metadata from step 1. If step 1 hasn't been
#        run (or a source database is missing from it), source
#        mapping resolution logs "Physical database not found" and
#        skips lineage for that table/column -- extraction itself
#        still succeeds, but source lineage will be incomplete.
#
#   3. fabric_report_extractor.py
#        Populates MetadataReport / -Page / -Visual / -VisualField /
#        -VisualFilter, and resolves report fields against the
#        semantic models from step 2. Requires step 2 to have already
#        synced the relevant semantic model(s), or report processing
#        fails with "... was not found in MetadataSemanticModel."
#
# AUTHENTICATION NOTE: these scripts use interactive Entra
# authentication (Authentication=ActiveDirectoryInteractive). When
# triggered from here, a browser window will open on THIS machine for
# you to sign in, same as running the script manually. This only
# works because the MCP server itself runs locally in your desktop
# session -- it will not work if this server is ever deployed
# somewhere headless/remote.

SCRIPTS_DIR = Path(
    r"C:\Projects\MetadataPlatform\extractors"
)

SQL_EXTRACTOR_SCRIPT = SCRIPTS_DIR / "sql_extractor.py"
SEMANTIC_MODEL_SCRIPT = SCRIPTS_DIR / "fabric_semantic_model_extractor.py"
REPORT_SCRIPT = SCRIPTS_DIR / "fabric_report_extractor.py"

JOBS_LOG_DIR = ROOT_DIR / "logs" / "extraction_jobs"
JOBS_LOG_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job tracking. Fine for a demo / single-instance server.
# Jobs are lost if the MCP server restarts -- use a small SQLite/JSON
# store instead if that matters for your use case.
_extraction_jobs: dict[str, dict[str, Any]] = {}


def _run_extraction_script(
    job_id: str,
    script_path: Path,
    job_label: str,
) -> None:

    log_path = JOBS_LOG_DIR / f"{job_id}.log"

    _extraction_jobs[job_id]["status"] = "running"
    _extraction_jobs[job_id]["log_path"] = str(log_path)
    _extraction_jobs[job_id]["started_at"] = (
        datetime.now(timezone.utc).isoformat()
    )

    logger.info(
        "Starting extraction job %s (%s). A browser window may open "
        "for Microsoft Entra sign-in -- please complete it to let "
        "the job proceed.",
        job_id,
        job_label,
    )

    try:

        with open(
            log_path,
            "w",
            encoding="utf-8",
            buffering=1,
        ) as log_file:

            env = {
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }

            process = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-u",
                    str(script_path),
                ],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(script_path.parent),
                env=env,
            )

        _extraction_jobs[job_id]["return_code"] = process.returncode

        _extraction_jobs[job_id]["status"] = (
            "succeeded" if process.returncode == 0 else "failed"
        )

    except Exception as exc:

        logger.exception(
            "Extraction job %s (%s) raised an exception.",
            job_id,
            job_label,
        )

        _extraction_jobs[job_id]["status"] = "failed"
        _extraction_jobs[job_id]["error"] = str(exc)

    finally:

        _extraction_jobs[job_id]["finished_at"] = (
            datetime.now(timezone.utc).isoformat()
        )

        logger.info(
            "Extraction job %s (%s) finished with status: %s",
            job_id,
            job_label,
            _extraction_jobs[job_id]["status"],
        )


def _start_extraction_job(
    script_path: Path,
    job_label: str,
) -> dict[str, Any]:

    if not script_path.exists():

        return {
            "error": (
                f"Script not found: {script_path}. "
                "Check SCRIPTS_DIR / script filename."
            )
        }

    job_id = str(uuid.uuid4())

    _extraction_jobs[job_id] = {
        "job_id": job_id,
        "label": job_label,
        "script": str(script_path),
        "status": "starting",
    }

    thread = threading.Thread(
        target=_run_extraction_script,
        args=(job_id, script_path, job_label),
        daemon=True,
    )

    thread.start()

    return {
        "job_id": job_id,
        "status": "starting",
        "note": (
            "A browser window may open for Microsoft Entra sign-in. "
            "Complete the sign-in on this machine to let the job "
            "proceed. Use get_extraction_status with this job_id to "
            "check progress."
        ),
    }


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
    Start the physical SQL source metadata extraction pipeline in
    the background. This WRITES to MetadataRepository (databases,
    tables, columns from the underlying SQL sources).

    A browser window may open on this machine for Microsoft Entra
    sign-in -- this must be completed manually for the job to
    proceed.

    Returns a job_id. Use get_extraction_status to check progress.

    Run this BEFORE start_semantic_model_extraction. Semantic model
    source-lineage resolution depends on this physical metadata
    already being present -- skipping this step doesn't fail
    semantic model extraction outright, but source lineage
    (MetadataSemanticTableSource / -ColumnSource) will be incomplete
    for any database not yet synced.
    """

    return _start_extraction_job(
        SQL_EXTRACTOR_SCRIPT,
        "SQL source extraction",
    )


# ============================================================================
# TOOL 13 - START SEMANTIC MODEL EXTRACTION
# ============================================================================

@mcp.tool()
def start_semantic_model_extraction() -> dict[str, Any]:
    """
    Start the Fabric semantic model metadata extraction pipeline in
    the background. This WRITES to MetadataRepository (tables,
    columns, measures, relationships, source lineage).

    A browser window may open on this machine for Microsoft Entra
    sign-in -- this must be completed manually for the job to
    proceed.

    Returns a job_id. Use get_extraction_status to check progress.
    This typically takes several minutes to complete.

    Run start_sql_extraction first if physical source metadata may
    be stale or missing. Run this BEFORE start_report_extraction,
    since report extraction depends on semantic models already being
    present in the repository.
    """

    return _start_extraction_job(
        SEMANTIC_MODEL_SCRIPT,
        "semantic model extraction",
    )


# ============================================================================
# TOOL 14 - START REPORT EXTRACTION
# ============================================================================

@mcp.tool()
def start_report_extraction() -> dict[str, Any]:
    """
    Start the Fabric report metadata extraction pipeline in the
    background. This WRITES to MetadataRepository (reports, pages,
    visuals, visual fields/filters, lineage).

    A browser window may open on this machine for Microsoft Entra
    sign-in -- this must be completed manually for the job to
    proceed.

    Returns a job_id. Use get_extraction_status to check progress.

    Requires semantic models to already exist in the repository --
    run start_semantic_model_extraction first if unsure.
    """

    return _start_extraction_job(
        REPORT_SCRIPT,
        "report extraction",
    )


# ============================================================================
# TOOL 15 - EXTRACTION JOB STATUS
# ============================================================================

@mcp.tool()
def get_extraction_status(
    job_id: str,
) -> dict[str, Any]:
    """
    Check the status of a previously started extraction job.

    Status values: starting, running, succeeded, failed.

    Returns the job's metadata plus the last portion of its log
    output, so progress and errors can be summarized without reading
    the full log file.
    """

    job = _extraction_jobs.get(job_id)

    if not job:
        return {
            "error": f"No extraction job found with id: {job_id}"
        }

    result = dict(job)

    # ------------------------------------------------------------------
    # Human-readable start time
    # ------------------------------------------------------------------

    if result.get("started_at"):
        result["started_at_display"] = (
            datetime.fromisoformat(result["started_at"])
            .astimezone()
            .strftime("%d %B %Y at %H:%M")
        )

    # ------------------------------------------------------------------
    # Human-readable finish time
    # ------------------------------------------------------------------

    if result.get("finished_at"):
        result["finished_at_display"] = (
            datetime.fromisoformat(result["finished_at"])
            .astimezone()
            .strftime("%d %B %Y at %H:%M")
        )

    # ------------------------------------------------------------------
    # Duration
    # ------------------------------------------------------------------

    if result.get("started_at"):

        started = datetime.fromisoformat(
            result["started_at"]
        )

        if result.get("finished_at"):

            finished = datetime.fromisoformat(
                result["finished_at"]
            )

            duration_seconds = int(
                (finished - started).total_seconds()
            )

        else:

            # Job is still running
            duration_seconds = int(
                (
                    datetime.now(timezone.utc)
                    - started
                ).total_seconds()
            )

        minutes, seconds = divmod(
            duration_seconds,
            60,
        )

        if minutes:
            result["duration_display"] = (
                f"{minutes} min {seconds:02d} sec"
            )
        else:
            result["duration_display"] = (
                f"{seconds} sec"
            )

    # ------------------------------------------------------------------
    # Log tail
    # ------------------------------------------------------------------

    log_path = job.get("log_path")

    if log_path:

        try:

            with open(
                log_path,
                "r",
                encoding="utf-8",
            ) as log_file:

                lines = log_file.readlines()

            result["log_tail"] = "".join(
                lines[-40:]
            )

        except FileNotFoundError:

            result["log_tail"] = ""

    return result


# ============================================================================
# TOOL 16 - LIST EXTRACTION JOBS
# ============================================================================

@mcp.tool()
def list_extraction_jobs() -> list[dict[str, Any]]:
    """
    List all extraction jobs started in this server session, most
    recent first, with their current status.
    """

    jobs = list(_extraction_jobs.values())

    jobs.sort(
        key=lambda job: job.get("started_at", ""),
        reverse=True,
    )

    return jobs


# ============================================================================
# TOOL 17 - RUN FULL METADATA REFRESH (SEQUENTIAL)
# ============================================================================

@mcp.tool()
def run_full_metadata_refresh() -> dict[str, Any]:
    """
    Run the full extraction pipeline in the correct order, waiting
    for each step to finish before starting the next:

        1. SQL source extraction
        2. Semantic model extraction
        3. Report extraction

    A browser window may open up to three times on this machine for
    Microsoft Entra sign-in -- each must be completed manually for
    the corresponding step to proceed.

    This call BLOCKS until all three steps finish or one fails, so
    it may take significant time (the semantic model and report
    steps alone have each taken 10-20+ minutes in testing). Returns
    a summary with each step's job_id and final status.

    Prefer start_sql_extraction / start_semantic_model_extraction /
    start_report_extraction individually if you want to run just one
    step, or want to avoid a single long-blocking call.
    """

    steps = [
        (
            "sql_extraction",
            SQL_EXTRACTOR_SCRIPT,
            "SQL source extraction",
        ),
        (
            "semantic_model_extraction",
            SEMANTIC_MODEL_SCRIPT,
            "semantic model extraction",
        ),
        (
            "report_extraction",
            REPORT_SCRIPT,
            "report extraction",
        ),
    ]

    results: dict[str, Any] = {"steps": []}

    for step_key, script_path, label in steps:

        start_result = _start_extraction_job(
            script_path,
            label,
        )

        if "error" in start_result:

            results["steps"].append(
                {
                    "step": step_key,
                    "error": start_result["error"],
                }
            )

            results["stopped_at"] = step_key

            return results

        job_id = start_result["job_id"]

        # Block until this step's background thread finishes before
        # starting the next one -- steps must run in order.
        while (
            _extraction_jobs[job_id]["status"]
            in ("starting", "running")
        ):

            threading.Event().wait(5)

        step_result = dict(
            _extraction_jobs[job_id]
        )

        results["steps"].append(
            {
                "step": step_key,
                "job_id": job_id,
                "status": step_result["status"],
            }
        )

        if step_result["status"] != "succeeded":

            results["stopped_at"] = step_key

            return results

    results["status"] = "all_steps_succeeded"

    return results


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
        "Extraction scripts directory: %s",
        SCRIPTS_DIR,
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

    mcp.run(transport="streamable-http")