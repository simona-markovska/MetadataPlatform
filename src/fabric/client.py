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
        """

        load_dotenv()

        self.access_token = os.getenv("FABRIC_ACCESS_TOKEN")

        if not self.access_token:
            raise ValueError(
                "FABRIC_ACCESS_TOKEN was not found in the .env file."
            )

        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        })

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
            []
        )

        return [
            item
            for item in items
            if item.get("type") == item_type
        ]

    def find_item_by_name(self, workspace_id, item_name):
        """
        Find a workspace item by its display name.

        Returns:
            dict: Matching item.
            None: If no matching item exists.
        """

        items = self.get_workspace_items(workspace_id).get(
            "value",
            []
        )

        for item in items:

            if item.get("displayName") == item_name:
                return item

        return None

    def get_semantic_model_definition(
        self,
        workspace_id,
        semantic_model_id
    ):
        """
        Get the actual definition of a Fabric semantic model.

        Fabric returns HTTP 202 and starts a long-running
        operation. Once that operation succeeds, the Location
        header points to the actual result endpoint.
        """

        url = (
            f"{self.BASE_URL}/workspaces/"
            f"{workspace_id}/semanticModels/"
            f"{semantic_model_id}/getDefinition"
        )

        print("\nSending semantic model definition request...")

        response = self.session.post(url)

        print("\n--- Initial Definition Request ---")
        print("Status:", response.status_code)

        response.raise_for_status()

        # ---------------------------------------------------------
        # Definition returned immediately
        # ---------------------------------------------------------

        if response.status_code == 200:

            print("Definition returned immediately.")

            return response.json()

        # ---------------------------------------------------------
        # Asynchronous operation
        # ---------------------------------------------------------

        if response.status_code == 202:

            operation_url = response.headers.get(
                "Location"
            )

            retry_after = int(
                response.headers.get(
                    "Retry-After",
                    "20"
                )
            )

            if not operation_url:

                raise RuntimeError(
                    "Fabric returned 202 but no operation URL "
                    "was provided."
                )

            print(
                f"Definition is being generated. "
                f"Waiting {retry_after} seconds..."
            )

            time.sleep(retry_after)

            # -----------------------------------------------------
            # Check operation status
            # -----------------------------------------------------

            operation_response = self.session.get(
                operation_url
            )

            operation_response.raise_for_status()

            operation_data = operation_response.json()

            status = operation_data.get(
                "status"
            )

            print(
                "\nOperation state:",
                status
            )

            if status == "Failed":

                raise RuntimeError(
                    f"Fabric definition operation failed: "
                    f"{operation_data}"
                )

            if status == "Cancelled":

                raise RuntimeError(
                    f"Fabric definition operation was cancelled: "
                    f"{operation_data}"
                )

            if status != "Succeeded":

                raise RuntimeError(
                    f"Unexpected operation status: "
                    f"{status}"
                )

            # -----------------------------------------------------
            # Get result URL
            # -----------------------------------------------------

            result_url = operation_response.headers.get(
                "Location"
            )

            if not result_url:

                raise RuntimeError(
                    "Operation succeeded but Fabric did not "
                    "provide a result URL."
                )

            print(
                "\nOperation succeeded."
            )

            print(
                "Getting definition result..."
            )

            # -----------------------------------------------------
            # Retrieve actual definition
            # -----------------------------------------------------

            result_response = self.session.get(
                result_url
            )

            print(
                "Definition result status:",
                result_response.status_code
            )

            result_response.raise_for_status()

            return result_response.json()

        # ---------------------------------------------------------
        # Unexpected status
        # ---------------------------------------------------------

        response.raise_for_status()

        return response.json()

    def get_report_definition(
        self,
        workspace_id,
        report_id
    ):
        """
        Get the actual definition of a Fabric report.

        Fabric may return HTTP 202 and start a long-running
        operation. Once that operation succeeds, the Location
        header points to the actual result endpoint.
        """

        url = (
            f"{self.BASE_URL}/workspaces/"
            f"{workspace_id}/reports/"
            f"{report_id}/getDefinition"
        )

        print("\nSending report definition request...")

        response = self.session.post(url)

        print("\n--- Initial Report Definition Request ---")
        print("Status:", response.status_code)

        response.raise_for_status()

        # ---------------------------------------------------------
        # Definition returned immediately
        # ---------------------------------------------------------

        if response.status_code == 200:

            print(
                "Report definition returned immediately."
            )

            return response.json()

        # ---------------------------------------------------------
        # Asynchronous operation
        # ---------------------------------------------------------

        if response.status_code == 202:

            operation_url = response.headers.get(
                "Location"
            )

            retry_after = int(
                response.headers.get(
                    "Retry-After",
                    "20"
                )
            )

            if not operation_url:

                raise RuntimeError(
                    "Fabric returned 202 but no operation URL "
                    "was provided."
                )

            print(
                f"Report definition is being generated. "
                f"Waiting {retry_after} seconds..."
            )

            time.sleep(retry_after)

            # -----------------------------------------------------
            # Check operation status
            # -----------------------------------------------------

            operation_response = self.session.get(
                operation_url
            )

            operation_response.raise_for_status()

            operation_data = operation_response.json()

            status = operation_data.get(
                "status"
            )

            print(
                "\nOperation state:",
                status
            )

            if status == "Failed":

                raise RuntimeError(
                    f"Fabric report definition operation failed: "
                    f"{operation_data}"
                )

            if status == "Cancelled":

                raise RuntimeError(
                    f"Fabric report definition operation was "
                    f"cancelled: {operation_data}"
                )

            if status != "Succeeded":

                raise RuntimeError(
                    f"Unexpected report definition operation "
                    f"status: {status}"
                )

            # -----------------------------------------------------
            # Get result URL
            # -----------------------------------------------------

            result_url = operation_response.headers.get(
                "Location"
            )

            if not result_url:

                raise RuntimeError(
                    "Report definition operation succeeded "
                    "but Fabric did not provide a result URL."
                )

            print(
                "\nReport definition operation succeeded."
            )

            print(
                "Getting report definition result..."
            )

            # -----------------------------------------------------
            # Retrieve actual definition
            # -----------------------------------------------------

            result_response = self.session.get(
                result_url
            )

            print(
                "Report definition result status:",
                result_response.status_code
            )

            result_response.raise_for_status()

            return result_response.json()

        # ---------------------------------------------------------
        # Unexpected status
        # ---------------------------------------------------------

        raise RuntimeError(
            "Unexpected response status while retrieving "
            f"report definition: {response.status_code}"
        )

