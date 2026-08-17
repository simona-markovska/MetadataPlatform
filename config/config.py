import os
from pathlib import Path


# ---------------------------------------------------------------------------
# SQL SOURCE CONFIGURATION
# ---------------------------------------------------------------------------

DEFAULT_DRIVER = os.getenv(
    "METADATA_SQL_DRIVER",
    "ODBC Driver 18 for SQL Server",
)

DEFAULT_SERVER = os.getenv(
    "METADATA_SQL_SERVER",
    "AXM345",
)

DEFAULT_SOURCE_DATABASE = os.getenv(
    "METADATA_SOURCE_DATABASE",
    "AdventureWorks2022",
)


# ---------------------------------------------------------------------------
# METADATA REPOSITORY CONFIGURATION
# ---------------------------------------------------------------------------

DEFAULT_REPOSITORY_DATABASE = os.getenv(
    "METADATA_REPOSITORY_DATABASE",
    "MetadataRepository",
)

DEFAULT_OUTPUT_DIR = os.getenv(
    "METADATA_OUTPUT_DIR",
    str(Path(__file__).resolve().parents[1] / "output"),
)


# ---------------------------------------------------------------------------
# SQL AUTHENTICATION
# ---------------------------------------------------------------------------

SQL_USERNAME = os.getenv(
    "METADATA_SQL_USERNAME",
)

SQL_PASSWORD = os.getenv(
    "METADATA_SQL_PASSWORD",
)


# ---------------------------------------------------------------------------
# FABRIC API CONFIGURATION
# ---------------------------------------------------------------------------

FABRIC_API_BASE_URL = os.getenv(
    "FABRIC_API_BASE_URL",
    "https://api.fabric.microsoft.com/v1",
)

FABRIC_WORKSPACE_ID = os.getenv(
    "FABRIC_WORKSPACE_ID",
)

# ---------------------------------------------------------------------------
# FABRIC METADATA REPOSITORY
# ---------------------------------------------------------------------------

FABRIC_SQL_SERVER = os.getenv(
    "METADATA_FABRIC_SQL_SERVER",
    "j7mjaqg22d2ujb27llpciiyism-7jnw46tiqcde5cpv233ctk345u.datawarehouse.fabric.microsoft.com",
)

FABRIC_SQL_DATABASE = os.getenv(
    "METADATA_FABRIC_SQL_DATABASE",
    "MetadataRepository",
)