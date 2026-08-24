import logging
import os
import time
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


logger = logging.getLogger(__name__)


class FabricClient:
    """
    Client for interacting with the Microsoft Fabric REST API.

    Responsibilities:
        - Authentication
        - HTTP communication
        - Workspace discovery
        - Workspace item discovery
        - Pagination
        - Fabric long-running operations
        - Report definition retrieval
        - Semantic model definition retrieval

    The client deliberately does NOT contain metadata extraction
    or repository-loading logic.
    """

    BASE_URL = "https://api.fabric.microsoft.com/v1"

    DEFAULT_TIMEOUT = 60

    MAX_RETRIES = 3

    RETRY_STATUS_CODES = {
        429,
        500,
        502,
        503,
        504,
    }

    def __init__(
        self,
        access_token: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ):
        """
        Initialize the Fabric REST API client.

        Authentication:
            FABRIC_ACCESS_TOKEN from .env

        Args:
            access_token:
                Optional explicit Fabric access token.

            timeout:
                HTTP request timeout in seconds.

            max_retries:
                Number of retries for transient HTTP failures.
        """

        load_dotenv()

        self.access_token = (
            access_token
            or os.getenv("FABRIC_ACCESS_TOKEN")
        )

        if not self.access_token:
            raise ValueError(
                "FABRIC_ACCESS_TOKEN was not found "
                "in the .env file."
            )

        self.timeout = timeout
        self.max_retries = max(
            int(max_retries),
            0,
        )

        self.session = requests.Session()

        self.session.headers.update(
            {
                "Authorization": (
                    f"Bearer {self.access_token}"
                ),
                "Content-Type": "application/json",
            }
        )

        logger.info(
            "FabricClient initialized."
        )

    # ======================================================================
    # HTTP HELPERS
    # ======================================================================

    def _request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> requests.Response:
        """
        Execute an HTTP request with timeout and transient retry handling.
        """

        kwargs.setdefault(
            "timeout",
            self.timeout,
        )

        last_response = None

        for attempt in range(
            self.max_retries + 1
        ):

            try:

                response = self.session.request(
                    method,
                    url,
                    **kwargs,
                )

                last_response = response

                if (
                    response.status_code
                    not in self.RETRY_STATUS_CODES
                ):

                    return response

                if attempt >= self.max_retries:

                    return response

                retry_after = self._get_retry_after(
                    response
                )

                logger.warning(
                    "Transient Fabric API response "
                    "%s. Retrying in %s seconds "
                    "(attempt %d/%d).",
                    response.status_code,
                    retry_after,
                    attempt + 1,
                    self.max_retries,
                )

                time.sleep(
                    retry_after
                )

            except requests.RequestException as exc:

                if attempt >= self.max_retries:

                    raise

                retry_after = min(
                    2 ** attempt,
                    10,
                )

                logger.warning(
                    "Fabric API request failed: %s. "
                    "Retrying in %s seconds "
                    "(attempt %d/%d).",
                    exc,
                    retry_after,
                    attempt + 1,
                    self.max_retries,
                )

                time.sleep(
                    retry_after
                )

        if last_response is not None:

            return last_response

        raise RuntimeError(
            "Fabric API request failed without "
            "returning a response."
        )

    @staticmethod
    def _get_retry_after(
        response: requests.Response,
        default: int = 5,
    ) -> int:
        """
        Get Retry-After from a response safely.
        """

        value = response.headers.get(
            "Retry-After"
        )

        try:

            return max(
                int(value),
                1,
            )

        except (
            TypeError,
            ValueError,
        ):

            return default

    @staticmethod
    def _json_response(
        response: requests.Response,
    ) -> Dict[str, Any]:
        """
        Validate and return a JSON response.
        """

        response.raise_for_status()

        try:

            data = response.json()

        except ValueError as exc:

            raise RuntimeError(
                "Fabric API returned a non-JSON response."
            ) from exc

        if not isinstance(
            data,
            dict,
        ):

            raise RuntimeError(
                "Fabric API returned unexpected JSON type: "
                f"{type(data).__name__}"
            )

        return data

    # ======================================================================
    # WORKSPACES
    # ======================================================================

    def get_workspaces(self):
        """
        Get all Fabric workspaces available to the authenticated user.
        """

        url = f"{self.BASE_URL}/workspaces"

        logger.info(
            "Retrieving Fabric workspaces."
        )

        response = self._request(
            "GET",
            url,
        )

        return self._json_response(
            response
        )

    def get_workspace(
        self,
        workspace_id: str,
    ):
        """
        Get information about a specific workspace.
        """

        if not workspace_id:

            raise ValueError(
                "workspace_id is required."
            )

        url = (
            f"{self.BASE_URL}/workspaces/"
            f"{workspace_id}"
        )

        logger.info(
            "Retrieving Fabric workspace: %s",
            workspace_id,
        )

        response = self._request(
            "GET",
            url,
        )

        return self._json_response(
            response
        )

    # ======================================================================
    # WORKSPACE ITEMS
    # ======================================================================

    def get_workspace_items(
        self,
        workspace_id: str,
    ):
        """
        Retrieve all items from a Fabric workspace.

        Pagination is handled automatically.

        Returns:
            {
                "value": [...]
            }
        """

        if not workspace_id:

            raise ValueError(
                "workspace_id is required."
            )

        url = (
            f"{self.BASE_URL}/workspaces/"
            f"{workspace_id}/items"
        )

        all_items = []
        page_number = 1

        logger.info(
            "Discovering Fabric workspace items: %s",
            workspace_id,
        )

        while url:

            logger.info(
                "Requesting workspace items page %d.",
                page_number,
            )

            response = self._request(
                "GET",
                url,
            )

            data = self._json_response(
                response
            )

            items = data.get(
                "value",
                [],
            )

            if not isinstance(
                items,
                list,
            ):

                raise RuntimeError(
                    "Unexpected Fabric workspace-items "
                    "'value' format."
                )

            all_items.extend(
                items
            )

            logger.info(
                "Workspace page %d returned %d items.",
                page_number,
                len(items),
            )

            url = (
                data.get("nextLink")
                or data.get("nextlink")
            )

            if url:

                page_number += 1

        logger.info(
            "Workspace discovery completed. "
            "Total items: %d.",
            len(all_items),
        )

        self._log_workspace_summary(
            all_items
        )

        return {
            "value": all_items
        }

    def get_items_by_type(
        self,
        workspace_id: str,
        item_type: str,
    ):
        """
        Get all workspace items of a specific type.

        Note:
            This method performs a fresh workspace discovery.
            For workflows processing multiple item types, prefer
            get_workspace_items() once and filter locally.
        """

        items = self.get_workspace_items(
            workspace_id
        ).get(
            "value",
            [],
        )

        return [
            item
            for item in items
            if item.get("type") == item_type
        ]

    def find_item_by_name(
        self,
        workspace_id: str,
        item_name: str,
        item_type: Optional[str] = None,
    ):
        """
        Find a workspace item by display name.

        Optionally restrict the search by item type.
        """

        items = self.get_workspace_items(
            workspace_id
        ).get(
            "value",
            [],
        )

        for item in items:

            if item.get(
                "displayName"
            ) != item_name:

                continue

            if (
                item_type
                and item.get("type")
                != item_type
            ):

                continue

            return item

        return None

    def print_workspace_inventory(
        self,
        workspace_id: str,
    ):
        """
        Print a readable workspace inventory.

        Primarily intended for manual diagnostics.
        """

        items = self.get_workspace_items(
            workspace_id
        ).get(
            "value",
            [],
        )

        print()
        print("=" * 70)
        print("FABRIC WORKSPACE INVENTORY")
        print("=" * 70)

        print(
            f"Total items: {len(items)}"
        )

        print()

        item_types = {}

        for item in items:

            item_type = item.get(
                "type",
                "Unknown",
            )

            item_types.setdefault(
                item_type,
                [],
            ).append(item)

        for item_type, type_items in sorted(
            item_types.items()
        ):

            print(
                f"{item_type}: "
                f"{len(type_items)}"
            )

            for item in type_items:

                print(
                    f"  - "
                    f"{item.get('displayName', '<Unnamed>')}"
                )

                print(
                    f"    ID: "
                    f"{item.get('id', '<No ID>')}"
                )

            print()

        print("=" * 70)

    @staticmethod
    def _log_workspace_summary(
        items,
    ):
        """
        Log a compact workspace inventory summary.
        """

        item_types = {}

        for item in items:

            if not isinstance(
                item,
                dict,
            ):

                continue

            item_type = item.get(
                "type",
                "Unknown",
            )

            item_types[
                item_type
            ] = (
                item_types.get(
                    item_type,
                    0,
                )
                + 1
            )

        if not item_types:
            return

        summary = ", ".join(
            f"{item_type}={count}"
            for item_type, count
            in sorted(
                item_types.items()
            )
        )

        logger.info(
            "Workspace inventory: %s",
            summary,
        )

    # ======================================================================
    # LONG-RUNNING OPERATIONS
    # ======================================================================

    def _wait_for_operation(
        self,
        operation_url: str,
        initial_retry_after: int = 10,
        max_wait_seconds: int = 300,
    ):
        """
        Wait for a Fabric long-running operation.

        Returns:
            requests.Response representing the successful
            operation response.
        """

        if not operation_url:

            raise ValueError(
                "operation_url is required."
            )

        elapsed = 0

        retry_after = max(
            int(
                initial_retry_after
                or 10
            ),
            1,
        )

        logger.info(
            "Waiting for Fabric operation."
        )

        while elapsed < max_wait_seconds:

            time.sleep(
                retry_after
            )

            elapsed += retry_after

            response = self._request(
                "GET",
                operation_url,
            )

            response.raise_for_status()

            try:

                data = response.json()

            except ValueError as exc:

                raise RuntimeError(
                    "Fabric operation returned "
                    "invalid JSON."
                ) from exc

            status = data.get(
                "status"
            )

            logger.info(
                "Fabric operation state: %s",
                status,
            )

            if status == "Succeeded":

                return response

            if status in {
                "Failed",
                "Cancelled",
            }:

                raise RuntimeError(
                    "Fabric operation did not succeed: "
                    f"{data}"
                )

            retry_after = self._get_retry_after(
                response
            )

        raise TimeoutError(
            "Fabric operation did not complete "
            f"within {max_wait_seconds} seconds."
        )

    # ======================================================================
    # DEFINITION RETRIEVAL
    # ======================================================================

    def _get_definition(
        self,
        workspace_id: str,
        item_type: str,
        item_id: str,
    ):
        """
        Generic Fabric definition retrieval.

        Supports:
            HTTP 200 - immediate result
            HTTP 202 - long-running operation
        """

        if not workspace_id:
            raise ValueError(
                "workspace_id is required."
            )

        if not item_id:
            raise ValueError(
                "item_id is required."
            )

        url = (
            f"{self.BASE_URL}/workspaces/"
            f"{workspace_id}/{item_type}/"
            f"{item_id}/getDefinition"
        )

        logger.info(
            "Requesting %s definition: %s",
            item_type,
            item_id,
        )

        response = self._request(
            "POST",
            url,
        )

        # ------------------------------------------------------------------
        # Immediate response
        # ------------------------------------------------------------------

        if response.status_code == 200:

            logger.info(
                "%s definition returned immediately.",
                item_type,
            )

            return self._json_response(
                response
            )

        # ------------------------------------------------------------------
        # Long-running operation
        # ------------------------------------------------------------------

        if response.status_code == 202:

            operation_url = response.headers.get(
                "Location"
            )

            retry_after = self._get_retry_after(
                response,
                default=10,
            )

            if not operation_url:

                raise RuntimeError(
                    f"Fabric returned 202 for {item_type} "
                    "definition but no operation URL "
                    "was provided."
                )

            logger.info(
                "%s definition is being generated. "
                "Initial wait: %d seconds.",
                item_type,
                retry_after,
            )

            operation_response = (
                self._wait_for_operation(
                    operation_url,
                    initial_retry_after=retry_after,
                )
            )

            result_url = (
                operation_response.headers.get(
                    "Location"
                )
            )

            if not result_url:

                raise RuntimeError(
                    f"{item_type} definition operation "
                    "succeeded but Fabric did not provide "
                    "a result URL."
                )

            logger.info(
                "Retrieving %s definition result.",
                item_type,
            )

            result_response = self._request(
                "GET",
                result_url,
            )

            return self._json_response(
                result_response
            )

        response.raise_for_status()

        raise RuntimeError(
            "Unexpected response status while retrieving "
            f"{item_type} definition: "
            f"{response.status_code}"
        )

    # ======================================================================
    # SEMANTIC MODEL DEFINITION
    # ======================================================================

    def get_semantic_model_definition(
        self,
        workspace_id: str,
        semantic_model_id: str,
    ):
        """
        Get the complete semantic model definition.
        """

        return self._get_definition(
            workspace_id,
            "semanticModels",
            semantic_model_id,
        )

    # ======================================================================
    # REPORT DEFINITION
    # ======================================================================

    def get_report_definition(
        self,
        workspace_id: str,
        report_id: str,
    ):
        """
        Get the complete report definition.
        """

        return self._get_definition(
            workspace_id,
            "reports",
            report_id,
        )

    # ======================================================================
    # CLEANUP
    # ======================================================================

    def close(self):
        """
        Close the underlying HTTP session.
        """

        self.session.close()

        logger.info(
            "FabricClient session closed."
        )

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        self.close()

