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


logger = logging.getLogger("metadata-mcp.extraction")


class ExtractionService:
    """
    Business/service layer for metadata extraction.

    This service orchestrates the production extraction scripts
    located under the extractors directory.

    Extraction order:

        1. SQL source extraction
        2. Semantic model extraction
        3. Report extraction

    The extractor scripts write metadata to the production
    MetadataRepository.

    This service does not contain metadata SQL.
    """

    def __init__(self):
        # ==================================================================
        # EXTRACTION SCRIPTS
        # ==================================================================

        self.scripts_dir = Path(
            r"C:\Projects\MetadataPlatform\extractors"
        )

        self.sql_extractor_script = (
            self.scripts_dir
            / "sql_extractor.py"
        )

        self.semantic_model_script = (
            self.scripts_dir
            / "fabric_semantic_model_extractor.py"
        )

        self.report_script = (
            self.scripts_dir
            / "fabric_report_extractor.py"
        )

        # ==================================================================
        # JOB LOG DIRECTORY
        # ==================================================================

        project_root = (
            Path(__file__).resolve().parents[2]
        )

        self.jobs_log_dir = (
            project_root
            / "logs"
            / "extraction_jobs"
        )

        self.jobs_log_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        # ==================================================================
        # IN-MEMORY JOB TRACKING
        # ==================================================================

        # Fine for the current demo / single-instance MCP server.
        #
        # Jobs are lost if the MCP server restarts.
        self.extraction_jobs: dict[
            str,
            dict[str, Any],
        ] = {}

    # ======================================================================
    # INTERNAL - RUN EXTRACTION SCRIPT
    # ======================================================================

    def _run_extraction_script(
        self,
        job_id: str,
        script_path: Path,
        job_label: str,
    ) -> None:
        """
        Run one extraction script in the background and
        update its job status.
        """

        log_path = (
            self.jobs_log_dir
            / f"{job_id}.log"
        )

        self.extraction_jobs[job_id][
            "status"
        ] = "running"

        self.extraction_jobs[job_id][
            "log_path"
        ] = str(log_path)

        self.extraction_jobs[job_id][
            "started_at"
        ] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        logger.info(
            "Starting extraction job %s (%s). "
            "A browser window may open for Microsoft "
            "Entra sign-in -- please complete it to let "
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

            self.extraction_jobs[job_id][
                "return_code"
            ] = process.returncode

            self.extraction_jobs[job_id][
                "status"
            ] = (
                "succeeded"
                if process.returncode == 0
                else "failed"
            )

        except Exception as exc:
            logger.exception(
                "Extraction job %s (%s) raised an exception.",
                job_id,
                job_label,
            )

            self.extraction_jobs[job_id][
                "status"
            ] = "failed"

            self.extraction_jobs[job_id][
                "error"
            ] = str(exc)

        finally:
            self.extraction_jobs[job_id][
                "finished_at"
            ] = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )

            logger.info(
                "Extraction job %s (%s) finished with status: %s",
                job_id,
                job_label,
                self.extraction_jobs[job_id][
                    "status"
                ],
            )

    # ======================================================================
    # INTERNAL - START EXTRACTION JOB
    # ======================================================================

    def _start_extraction_job(
        self,
        script_path: Path,
        job_label: str,
    ) -> dict[str, Any]:
        """
        Create and start a background extraction job.
        """

        if not script_path.exists():
            return {
                "error": (
                    f"Script not found: {script_path}. "
                    "Check extraction script path and filename."
                )
            }

        job_id = str(
            uuid.uuid4()
        )

        self.extraction_jobs[job_id] = {
            "job_id": job_id,
            "label": job_label,
            "script": str(script_path),
            "status": "starting",
        }

        thread = threading.Thread(
            target=self._run_extraction_script,
            args=(
                job_id,
                script_path,
                job_label,
            ),
            daemon=True,
        )

        thread.start()

        return {
            "job_id": job_id,
            "status": "starting",
            "note": (
                "A browser window may open for Microsoft "
                "Entra sign-in. Complete the sign-in on "
                "this machine to let the job proceed. "
                "Use get_extraction_status with this "
                "job_id to check progress."
            ),
        }

    # ======================================================================
    # SQL SOURCE EXTRACTION
    # ======================================================================

    def start_sql_extraction(
        self,
    ) -> dict[str, Any]:
        """
        Start the physical SQL source metadata extraction pipeline.

        This populates the physical SQL metadata used by downstream
        semantic-model source lineage resolution.
        """

        return self._start_extraction_job(
            self.sql_extractor_script,
            "SQL source extraction",
        )

    # ======================================================================
    # SEMANTIC MODEL EXTRACTION
    # ======================================================================

    def start_semantic_model_extraction(
        self,
    ) -> dict[str, Any]:
        """
        Start the Fabric semantic model metadata extraction pipeline.

        This extracts semantic models, tables, columns, measures,
        relationships and source mappings.
        """

        return self._start_extraction_job(
            self.semantic_model_script,
            "semantic model extraction",
        )

    # ======================================================================
    # REPORT EXTRACTION
    # ======================================================================

    def start_report_extraction(
        self,
    ) -> dict[str, Any]:
        """
        Start the Fabric report metadata extraction pipeline.

        This extracts reports, pages, visuals, visual fields,
        visual filters and report lineage.
        """

        return self._start_extraction_job(
            self.report_script,
            "report extraction",
        )

    # ======================================================================
    # EXTRACTION JOB STATUS
    # ======================================================================

    def get_extraction_status(
        self,
        job_id: str,
    ) -> dict[str, Any]:
        """
        Return the current status and recent log output
        for an extraction job.
        """

        job = self.extraction_jobs.get(
            job_id
        )

        if not job:
            return {
                "error": (
                    f"No extraction job found with id: "
                    f"{job_id}"
                )
            }

        result = dict(job)

        # ------------------------------------------------------------------
        # HUMAN-READABLE START TIME
        # ------------------------------------------------------------------

        if result.get("started_at"):
            result["started_at_display"] = (
                datetime.fromisoformat(
                    result["started_at"]
                )
                .astimezone()
                .strftime(
                    "%d %B %Y at %H:%M"
                )
            )

        # ------------------------------------------------------------------
        # HUMAN-READABLE FINISH TIME
        # ------------------------------------------------------------------

        if result.get("finished_at"):
            result["finished_at_display"] = (
                datetime.fromisoformat(
                    result["finished_at"]
                )
                .astimezone()
                .strftime(
                    "%d %B %Y at %H:%M"
                )
            )

        # ------------------------------------------------------------------
        # DURATION
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
                    (
                        finished - started
                    ).total_seconds()
                )

            else:
                # Job is still running.
                duration_seconds = int(
                    (
                        datetime.now(
                            timezone.utc
                        )
                        - started
                    ).total_seconds()
                )

            minutes, seconds = divmod(
                duration_seconds,
                60,
            )

            if minutes:
                result["duration_display"] = (
                    f"{minutes} min "
                    f"{seconds:02d} sec"
                )
            else:
                result["duration_display"] = (
                    f"{seconds} sec"
                )

        # ------------------------------------------------------------------
        # LOG TAIL
        # ------------------------------------------------------------------

        log_path = result.get(
            "log_path"
        )

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

    # ======================================================================
    # LIST EXTRACTION JOBS
    # ======================================================================

    def list_extraction_jobs(
        self,
    ) -> list[dict[str, Any]]:
        """
        Return all extraction jobs started during
        the current MCP server session.

        Most recent jobs are returned first.
        """

        jobs = list(
            self.extraction_jobs.values()
        )

        jobs.sort(
            key=lambda job: job.get(
                "started_at",
                "",
            ),
            reverse=True,
        )

        return jobs

    # ======================================================================
    # FULL METADATA REFRESH
    # ======================================================================

    def run_full_metadata_refresh(
        self,
    ) -> dict[str, Any]:
        """
        Run the complete metadata extraction pipeline sequentially.

        Order:

            1. SQL source extraction
            2. Semantic model extraction
            3. Report extraction

        Each step must finish successfully before the next step
        starts.

        If one step fails, the pipeline stops and reports which
        step failed.
        """

        steps = [
            (
                "sql_extraction",
                self.sql_extractor_script,
                "SQL source extraction",
            ),
            (
                "semantic_model_extraction",
                self.semantic_model_script,
                "semantic model extraction",
            ),
            (
                "report_extraction",
                self.report_script,
                "report extraction",
            ),
        ]

        results: dict[str, Any] = {
            "steps": []
        }

        for (
            step_key,
            script_path,
            label,
        ) in steps:

            start_result = (
                self._start_extraction_job(
                    script_path,
                    label,
                )
            )

            if "error" in start_result:
                results["steps"].append(
                    {
                        "step": step_key,
                        "error": start_result[
                            "error"
                        ],
                    }
                )

                results["stopped_at"] = (
                    step_key
                )

                return results

            job_id = start_result[
                "job_id"
            ]

            # --------------------------------------------------------------
            # Wait until the current extraction step finishes.
            # --------------------------------------------------------------

            while (
                self.extraction_jobs[job_id][
                    "status"
                ]
                in (
                    "starting",
                    "running",
                )
            ):
                threading.Event().wait(5)

            step_result = dict(
                self.extraction_jobs[job_id]
            )

            results["steps"].append(
                {
                    "step": step_key,
                    "job_id": job_id,
                    "status": step_result[
                        "status"
                    ],
                }
            )

            if (
                step_result["status"]
                != "succeeded"
            ):
                results["stopped_at"] = (
                    step_key
                )

                return results

        results["status"] = (
            "all_steps_succeeded"
        )

        return results