"""
Metadata Intelligence Platform
Fabric Warehouse repository connection.

This module is responsible only for establishing
connections to the MetadataRepository warehouse.

It contains no MCP logic and no business logic.
"""

from __future__ import annotations

import logging

import pyodbc

from config.config import DEFAULT_DRIVER


# ============================================================================
# LOGGING
# ============================================================================

logger = logging.getLogger("metadata-repository")


# ============================================================================
# FABRIC CONFIGURATION
# ============================================================================

FABRIC_SQL_SERVER = (
    "j7mjaqg22d2ujb27llpciiyism-7jnw46tiqcde5cpv233ctk345u"
    ".datawarehouse.fabric.microsoft.com"
)

FABRIC_SQL_DATABASE = "MetadataRepository"


# ============================================================================
# CONNECTION
# ============================================================================

def get_connection_string() -> str:
    """
    Build the Fabric Warehouse ODBC connection string.

    Authentication is interactive for local development.
    """

    return (
        f"DRIVER={{{DEFAULT_DRIVER}}};"
        f"SERVER={FABRIC_SQL_SERVER};"
        f"DATABASE={FABRIC_SQL_DATABASE};"
        "Authentication=ActiveDirectoryInteractive;"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
    )


def get_connection() -> pyodbc.Connection:
    """
    Open a connection to the MetadataRepository Fabric Warehouse.
    """

    logger.info(
        "Opening MetadataRepository connection..."
    )

    connection = pyodbc.connect(
        get_connection_string()
    )

    logger.info(
        "Connected to MetadataRepository."
    )

    return connection