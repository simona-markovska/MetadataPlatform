from __future__ import annotations

import logging
import time
from typing import Any

from src.services.extraction_service import ExtractionService


logger = logging.getLogger("metadata-mcp.extraction-monitor")


class ExtractionMonitorService:
    """
    Agent-facing monitoring layer for metadata extraction.

    ExtractionService is responsible for starting and running
    extraction jobs.

    This service is responsible for checking the status of
    already-started extraction jobs.

    Workflow:

        1. STARTED
        2. RUNNING
        3. COMPLETED / FAILED

    IMPORTANT:
    This service does not start extraction jobs.

    The corresponding start_* tool must be called first.

    If a job is still running when checked, this service waits
    briefly before returning, so repeated agent calls don't need
    to poll back-to-back instantly. It still does not wait for
    full completion -- each call waits at most wait_seconds before
    returning the latest status.
    """

    def __init__(
        self,
        extraction_service: ExtractionService,
    ):
        self.extraction_service = extraction_service

    # ======================================================================
    # INTERNAL - CHECK ONE JOB
    # ======================================================================

    def _monitor_job(
        self,
        job_id: str,
    ) -> dict[str, Any]:
        """
        Check the current status of an existing extraction job.

        This performs exactly one status check.

        It does not:
        - start the extraction
        - wait for completion
        - poll repeatedly
        """

        status = self.extraction_service.get_extraction_status(
            job_id
        )

        if "error" in status:
            return {
                "status": "failed",
                "job_id": job_id,
                "error": status["error"],
            }

        return status

    # ======================================================================
    # GENERIC MONITOR
    # ======================================================================

    def _monitor_existing_job(
        self,
        job_id: str,
        extraction_name: str,
        wait_seconds: int = 300,
    ) -> dict[str, Any]:
        """
        Check the status of an already-started extraction job.

        The corresponding start_* tool must be called first.

        If the job is still running, this waits up to
        wait_seconds before returning, then re-checks once so
        the response reflects the freshest status. This lets an
        agent poll in a loop without each call returning
        instantly while the job is still in progress.
        """

        logger.info(
            "Checking %s job %s.",
            extraction_name,
            job_id,
        )

        current_status = self._monitor_job(
            job_id
        )

        if "error" in current_status:
            return {
                "status": "failed",
                "stage": "monitoring",
                "extraction": extraction_name,
                "job_id": job_id,
                "error": current_status["error"],
            }

        job_status = current_status.get(
            "status"
        )

        # --------------------------------------------------------------
        # IF STILL IN PROGRESS, WAIT THEN RE-CHECK ONCE
        # --------------------------------------------------------------

        if job_status in (
            "starting",
            "running",
        ):
            time.sleep(
                wait_seconds
            )

            current_status = self._monitor_job(
                job_id
            )

            if "error" in current_status:
                return {
                    "status": "failed",
                    "stage": "monitoring",
                    "extraction": extraction_name,
                    "job_id": job_id,
                    "error": current_status["error"],
                }

            job_status = current_status.get(
                "status"
            )

        if job_status == "succeeded":
            stage = "completed"
            completed = True

        elif job_status == "failed":
            stage = "completed"
            completed = True

        else:
            stage = "running"
            completed = False

        result: dict[str, Any] = {
            "extraction": extraction_name,
            "job_id": job_id,
            "status": job_status,
            "stage": stage,
            "completed": completed,
            "started_at": current_status.get(
                "started_at_display"
            ),
            "finished_at": current_status.get(
                "finished_at_display"
            ),
            "duration": current_status.get(
                "duration_display"
            ),
            "return_code": current_status.get(
                "return_code"
            ),
        }

        # --------------------------------------------------------------
        # FINAL SUMMARY
        # --------------------------------------------------------------

        if completed:
            result["summary"] = current_status.get(
                "log_tail",
                "",
            )

        # --------------------------------------------------------------
        # CURRENT STATUS
        # --------------------------------------------------------------

        if not completed:
            result["message"] = (
                f"{extraction_name} is still running."
            )

        else:
            if job_status == "succeeded":
                result["message"] = (
                    f"{extraction_name} completed successfully."
                )
            else:
                result["message"] = (
                    f"{extraction_name} completed with failure."
                )

        # Keep the complete status available for the agent
        # if additional details are required.
        result["final_status"] = current_status

        return result

    # ======================================================================
    # SQL
    # ======================================================================

    def monitor_sql_extraction(
        self,
        job_id: str,
    ) -> dict[str, Any]:
        """
        Check the status of an already-started SQL source metadata
        extraction job.

        The extraction must first be started using
        start_sql_extraction.

        If the job is still running, this call waits briefly
        before returning an updated status. It does not start
        another extraction.
        """

        return self._monitor_existing_job(
            job_id,
            "SQL source extraction",
        )

    # ======================================================================
    # SEMANTIC MODEL
    # ======================================================================

    def monitor_semantic_model_extraction(
        self,
        job_id: str,
    ) -> dict[str, Any]:
        """
        Check the status of an already-started semantic model
        extraction job.

        The extraction must first be started using
        start_semantic_model_extraction.

        If the job is still running, this call waits briefly
        before returning an updated status. It does not start
        another extraction.
        """

        return self._monitor_existing_job(
            job_id,
            "semantic model extraction",
        )

    # ======================================================================
    # REPORT
    # ======================================================================

    def monitor_report_extraction(
        self,
        job_id: str,
    ) -> dict[str, Any]:
        """
        Check the status of an already-started report extraction job.

        The extraction must first be started using
        start_report_extraction.

        If the job is still running, this call waits briefly
        before returning an updated status. It does not start
        another extraction.
        """

        return self._monitor_existing_job(
            job_id,
            "report extraction",
        )

    # ======================================================================
    # FULL REFRESH
    # ======================================================================

    def monitor_full_metadata_refresh(
        self,
        job_id: str,
    ) -> dict[str, Any]:
        """
        Check the status of an already-started full metadata refresh.

        The full refresh must first be started using
        run_full_metadata_refresh.

        If the job is still running, this call waits briefly
        before returning an updated status. It does not start
        another refresh.
        """

        return self._monitor_existing_job(
            job_id,
            "full metadata refresh",
        )