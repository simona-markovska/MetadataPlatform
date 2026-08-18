import os
import time

import requests
from dotenv import load_dotenv


class FabricClient:
    """
    Client for interacting with the Microsoft Fabric REST API.
    """

    BASE_URL = "https://api.fabric.microsoft.com/v1"

    def __init__(self):
        """
        Initialize the Fabric client.

        FABRIC_ACCESS_TOKEN remains the only Fabric API
        authentication value required from .env.
        """

        load_dotenv()

        self.access_token = os.getenv("FABRIC_ACCESS_TOKEN")

        if not self.access_token:
            raise ValueError(
                "FABRIC_ACCESS_TOKEN was not found in the .env file."
            )

        self.session = requests.Session()

        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }
        )

    # =======================================================================
    # WORKSPACES
    # =======================================================================

    def get_workspaces(self):
        """
        Get all Fabric workspaces available to the authenticated user.
        """

        url = f"{self.BASE_URL}/workspaces"

        response = self.session.get(url)

        response.raise_for_status()

        return response.json()

    def get_workspace(self, workspace_id):
        """
        Get information about a specific workspace.
        """

        url = f"{self.BASE_URL}/workspaces/{workspace_id}"

        response = self.session.get(url)

        response.raise_for_status()

        return response.json()

    # =======================================================================
    # WORKSPACE ITEMS
    # =======================================================================

    def get_workspace_items(self, workspace_id):
        """
        Get all items inside a Fabric workspace.
        """

        url = f"{self.BASE_URL}/workspaces/{workspace_id}/items"

        response = self.session.get(url)

        response.raise_for_status()

        return response.json()

    def get_items_by_type(self, workspace_id, item_type):
        """
        Get all workspace items of a specific Fabric type.
        """

        items = self.get_workspace_items(workspace_id).get(
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
        workspace_id,
        item_name,
        item_type=None,
    ):
        """
        Find a workspace item by display name.

        Optionally restrict the search to a specific item type.
        """

        items = self.get_workspace_items(workspace_id).get(
            "value",
            [],
        )

        for item in items:

            if item.get("displayName") != item_name:
                continue

            if item_type and item.get("type") != item_type:
                continue

            return item

        return None

    # =======================================================================
    # ASYNC OPERATION HELPER
    # =======================================================================

    def _wait_for_operation(
        self,
        operation_url,
        initial_retry_after=10,
        max_wait_seconds=300,
    ):
        """
        Wait for a Fabric long-running operation to complete.

        Returns:
            operation response JSON
        """

        elapsed = 0
        retry_after = max(
            int(initial_retry_after or 10),
            1,
        )

        while elapsed < max_wait_seconds:

            time.sleep(retry_after)

            elapsed += retry_after

            response = self.session.get(
                operation_url
            )

            response.raise_for_status()

            data = response.json()

            status = data.get("status")

            print(
                f"Operation state: {status}"
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

            retry_after = int(
                response.headers.get(
                    "Retry-After",
                    10,
                )
            )

            retry_after = max(
                retry_after,
                1,
            )

        raise TimeoutError(
            "Fabric operation did not complete within "
            f"{max_wait_seconds} seconds."
        )

    # =======================================================================
    # SEMANTIC MODEL DEFINITION
    # =======================================================================

    def get_semantic_model_definition(
        self,
        workspace_id,
        semantic_model_id,
    ):
        """
        Get the complete definition of a Fabric semantic model.

        Fabric may return HTTP 200 immediately or HTTP 202
        and start a long-running operation.
        """

        url = (
            f"{self.BASE_URL}/workspaces/"
            f"{workspace_id}/semanticModels/"
            f"{semantic_model_id}/getDefinition"
        )

        print(
            "\nSending semantic model definition request..."
        )

        response = self.session.post(url)

        print(
            "\n--- Initial Definition Request ---"
        )

        print(
            "Status:",
            response.status_code,
        )

        response.raise_for_status()

        # -------------------------------------------------------------------
        # Immediate result
        # -------------------------------------------------------------------

        if response.status_code == 200:

            print(
                "Definition returned immediately."
            )

            return response.json()

        # -------------------------------------------------------------------
        # Asynchronous operation
        # -------------------------------------------------------------------

        if response.status_code == 202:

            operation_url = response.headers.get(
                "Location"
            )

            retry_after = int(
                response.headers.get(
                    "Retry-After",
                    10,
                )
            )

            if not operation_url:

                raise RuntimeError(
                    "Fabric returned 202 but no operation URL "
                    "was provided."
                )

            print(
                "Definition is being generated. "
                f"Waiting {retry_after} seconds..."
            )

            operation_response = self._wait_for_operation(
                operation_url,
                initial_retry_after=retry_after,
            )

            result_url = operation_response.headers.get(
                "Location"
            )

            if not result_url:

                raise RuntimeError(
                    "Semantic model definition operation "
                    "succeeded but Fabric did not provide "
                    "a result URL."
                )

            print(
                "\nOperation succeeded."
            )

            print(
                "Getting definition result..."
            )

            result_response = self.session.get(
                result_url
            )

            print(
                "Definition result status:",
                result_response.status_code,
            )

            result_response.raise_for_status()

            return result_response.json()

        raise RuntimeError(
            "Unexpected response status while retrieving "
            "semantic model definition: "
            f"{response.status_code}"
        )

    # =======================================================================
    # REPORT DEFINITION
    # =======================================================================

    def get_report_definition(
        self,
        workspace_id,
        report_id,
    ):
        """
        Get the complete definition of a Fabric report.

        Fabric may return HTTP 200 immediately or HTTP 202
        and start a long-running operation.
        """

        url = (
            f"{self.BASE_URL}/workspaces/"
            f"{workspace_id}/reports/"
            f"{report_id}/getDefinition"
        )

        print(
            "\nSending report definition request..."
        )

        response = self.session.post(url)

        print(
            "\n--- Initial Report Definition Request ---"
        )

        print(
            "Status:",
            response.status_code,
        )

        response.raise_for_status()

        # -------------------------------------------------------------------
        # Immediate result
        # -------------------------------------------------------------------

        if response.status_code == 200:

            print(
                "Report definition returned immediately."
            )

            return response.json()

        # -------------------------------------------------------------------
        # Asynchronous operation
        # -------------------------------------------------------------------

        if response.status_code == 202:

            operation_url = response.headers.get(
                "Location"
            )

            retry_after = int(
                response.headers.get(
                    "Retry-After",
                    10,
                )
            )

            if not operation_url:

                raise RuntimeError(
                    "Fabric returned 202 but no operation URL "
                    "was provided."
                )

            print(
                "Report definition is being generated. "
                f"Waiting {retry_after} seconds..."
            )

            operation_response = self._wait_for_operation(
                operation_url,
                initial_retry_after=retry_after,
            )

            result_url = operation_response.headers.get(
                "Location"
            )

            if not result_url:

                raise RuntimeError(
                    "Report definition operation succeeded "
                    "but Fabric did not provide "
                    "a result URL."
                )

            print(
                "\nReport definition operation succeeded."
            )

            print(
                "Getting report definition result..."
            )

            result_response = self.session.get(
                result_url
            )

            print(
                "Report definition result status:",
                result_response.status_code,
            )

            result_response.raise_for_status()

            return result_response.json()

        raise RuntimeError(
            "Unexpected response status while retrieving "
            "report definition: "
            f"{response.status_code}"
        )

