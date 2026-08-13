import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure the workspace root is on sys.path when this script is executed directly.
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import pyodbc

from config.config import (
    DEFAULT_DRIVER,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REPOSITORY_DATABASE,
    DEFAULT_SERVER,
    DEFAULT_SOURCE_DATABASE,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


# Fabric Warehouse SQL endpoint
FABRIC_SQL_SERVER = (
    "j7mjaqg22d2ujb27llpciiyism-7jnw46tiqcde5cpv233ctk345u.datawarehouse.fabric.microsoft.com"
)

# Fabric Warehouse database name
FABRIC_SQL_DATABASE = "MetadataRepository"


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# SOURCE DATABASE CONNECTION
# ---------------------------------------------------------------------------
#
# Source:
#   Server   : AXM345
#   Database : AdventureWorks2022
#
# Authentication:
#   Windows Authentication
#
# This is the original source authentication.
# ---------------------------------------------------------------------------

def get_source_connection_string(
    driver: str,
    server: str,
    database: str,
) -> str:

    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )


def connect_to_source(
    driver: str,
    server: str,
    database: str,
) -> pyodbc.Connection:

    connection_string = get_source_connection_string(
        driver,
        server,
        database,
    )

    return pyodbc.connect(connection_string)


# ---------------------------------------------------------------------------
# FABRIC WAREHOUSE DESTINATION CONNECTION
# ---------------------------------------------------------------------------
#
# Destination:
#   Fabric Warehouse
#
# Authentication:
#   Microsoft Entra Interactive Authentication
#
# This is the same authentication method that was successfully tested
# in the standalone Fabric Metadata Repository test.
# ---------------------------------------------------------------------------

def get_fabric_connection_string(
    driver: str,
    server: str,
    database: str,
) -> str:

    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Authentication=ActiveDirectoryInteractive;"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
    )


def connect_to_fabric_warehouse(
    driver: str,
    server: str,
    database: str,
) -> pyodbc.Connection:

    connection_string = get_fabric_connection_string(
        driver,
        server,
        database,
    )

    return pyodbc.connect(connection_string)


# ---------------------------------------------------------------------------
# Metadata database
# ---------------------------------------------------------------------------

def get_database_id(
    cursor: pyodbc.Cursor,
    database_name: str,
    server_name: str,
) -> int | None:

    cursor.execute(
        """
        SELECT DatabaseID
        FROM MetadataDatabase
        WHERE DatabaseName = ?
          AND ServerName = ?
        """,
        database_name,
        server_name,
    )

    result = cursor.fetchone()

    return result[0] if result else None


def insert_database(
    cursor: pyodbc.Cursor,
    database_name: str,
    server_name: str,
) -> int:

    cursor.execute(
        """
        INSERT INTO MetadataDatabase
        (
            DatabaseName,
            ServerName
        )
        VALUES (?, ?)
        """,
        database_name,
        server_name,
    )

    cursor.connection.commit()

    database_id = get_database_id(
        cursor,
        database_name,
        server_name,
    )

    if database_id is None:
        raise RuntimeError(
            "Failed to retrieve DatabaseID after inserting database."
        )

    return database_id


# ---------------------------------------------------------------------------
# Extraction logging
# ---------------------------------------------------------------------------

def start_extraction_log(
    cursor: pyodbc.Cursor,
    database_id: int,
) -> int:

    start_time = datetime.now()

    # Fabric Warehouse does not support:
    #
    # OUTPUT INSERTED.ExtractionID
    #
    # Therefore, insert the record first and then retrieve
    # the generated identity value.

    cursor.execute(
        """
        INSERT INTO MetadataExtractionLog
        (
            DatabaseID,
            StartTime,
            Status
        )
        VALUES (?, ?, ?)
        """,
        database_id,
        start_time,
        "Running",
    )

    cursor.connection.commit()

    cursor.execute(
        """
        SELECT MAX(ExtractionID)
        FROM MetadataExtractionLog
        WHERE DatabaseID = ?
          AND StartTime = ?
          AND Status = ?
        """,
        database_id,
        start_time,
        "Running",
    )

    identity_result = cursor.fetchone()

    if not identity_result or identity_result[0] is None:
        cursor.connection.rollback()

        raise RuntimeError(
            "Failed to retrieve ExtractionID after starting extraction log."
        )

    extraction_id = int(identity_result[0])

    return extraction_id


def update_extraction_log(
    cursor: pyodbc.Cursor,
    extraction_id: int,
    status: str,
    tables_loaded: int | None = None,
    columns_loaded: int | None = None,
    relationships_loaded: int | None = None,
    error_message: str | None = None,
) -> None:

    columns = [
        "EndTime = ?",
        "Status = ?",
    ]

    params = [
        datetime.now(),
        status,
    ]

    if tables_loaded is not None:
        columns.append("TablesLoaded = ?")
        params.append(tables_loaded)

    if columns_loaded is not None:
        columns.append("ColumnsLoaded = ?")
        params.append(columns_loaded)

    if relationships_loaded is not None:
        columns.append("RelationshipsLoaded = ?")
        params.append(relationships_loaded)

    if error_message is not None:
        columns.append("ErrorMessage = ?")
        params.append(error_message)

    params.append(extraction_id)

    cursor.execute(
        f"""
        UPDATE MetadataExtractionLog
        SET {", ".join(columns)}
        WHERE ExtractionID = ?
        """,
        *params,
    )

    cursor.connection.commit()


# ---------------------------------------------------------------------------
# Extract tables
# ---------------------------------------------------------------------------

def extract_tables(
    source_conn: pyodbc.Connection,
) -> pd.DataFrame:

    tables_query = """
    SELECT
        TABLE_SCHEMA,
        TABLE_NAME,
        TABLE_TYPE
    FROM INFORMATION_SCHEMA.TABLES
    ORDER BY
        TABLE_SCHEMA,
        TABLE_NAME;
    """

    tables = pd.read_sql(
        tables_query,
        source_conn,
    )

    logging.info(
        "Extracted %d tables from source database",
        len(tables),
    )

    return tables


# ---------------------------------------------------------------------------
# Extract columns
# ---------------------------------------------------------------------------

def extract_columns(
    source_conn: pyodbc.Connection,
) -> pd.DataFrame:

    columns_query = """
    SELECT
        TABLE_SCHEMA,
        TABLE_NAME,
        ORDINAL_POSITION,
        COLUMN_NAME,
        DATA_TYPE,
        CHARACTER_MAXIMUM_LENGTH,
        IS_NULLABLE
    FROM INFORMATION_SCHEMA.COLUMNS
    ORDER BY
        TABLE_SCHEMA,
        TABLE_NAME,
        ORDINAL_POSITION;
    """

    columns = pd.read_sql(
        columns_query,
        source_conn,
    )

    logging.info(
        "Extracted %d columns from source database",
        len(columns),
    )

    return columns


# ---------------------------------------------------------------------------
# Extract relationships
# ---------------------------------------------------------------------------

def extract_relationships(
    source_conn: pyodbc.Connection,
) -> pd.DataFrame:

    relationships_query = """
    SELECT
        fk.name AS CONSTRAINT_NAME,

        parent_schema.name AS PARENT_SCHEMA,
        parent_table.name AS PARENT_TABLE,
        parent_column.name AS PARENT_COLUMN,

        child_schema.name AS CHILD_SCHEMA,
        child_table.name AS CHILD_TABLE,
        child_column.name AS CHILD_COLUMN

    FROM sys.foreign_keys fk

    INNER JOIN sys.foreign_key_columns fkc
        ON fk.object_id = fkc.constraint_object_id

    INNER JOIN sys.tables parent_table
        ON fkc.referenced_object_id = parent_table.object_id

    INNER JOIN sys.schemas parent_schema
        ON parent_table.schema_id = parent_schema.schema_id

    INNER JOIN sys.columns parent_column
        ON parent_column.object_id = fkc.referenced_object_id
        AND parent_column.column_id = fkc.referenced_column_id

    INNER JOIN sys.tables child_table
        ON fkc.parent_object_id = child_table.object_id

    INNER JOIN sys.schemas child_schema
        ON child_table.schema_id = child_schema.schema_id

    INNER JOIN sys.columns child_column
        ON child_column.object_id = fkc.parent_object_id
        AND child_column.column_id = fkc.parent_column_id

    ORDER BY
        parent_schema.name,
        parent_table.name,
        fk.name,
        fkc.constraint_column_id;
    """

    relationships = pd.read_sql(
        relationships_query,
        source_conn,
    )

    logging.info(
        "Extracted %d relationship column mappings from source database",
        len(relationships),
    )

    return relationships


# ---------------------------------------------------------------------------
# Load tables
# ---------------------------------------------------------------------------

def load_tables(
    cursor: pyodbc.Cursor,
    database_id: int,
    tables: pd.DataFrame,
) -> int:

    existing_tables = {}

    cursor.execute(
        """
        SELECT
            TableID,
            SchemaName,
            TableName
        FROM MetadataTable
        WHERE DatabaseID = ?
        """,
        database_id,
    )

    for table_id, schema_name, table_name in cursor.fetchall():
        existing_tables[
            (schema_name, table_name)
        ] = table_id

    table_inserts = 0

    for _, row in tables.iterrows():

        key = (
            row["TABLE_SCHEMA"],
            row["TABLE_NAME"],
        )

        if key in existing_tables:
            continue

        cursor.execute(
            """
            INSERT INTO MetadataTable
            (
                DatabaseID,
                SchemaName,
                TableName,
                TableType
            )
            VALUES (?, ?, ?, ?)
            """,
            database_id,
            row["TABLE_SCHEMA"],
            row["TABLE_NAME"],
            row["TABLE_TYPE"],
        )

        table_inserts += 1

    cursor.connection.commit()

    logging.info(
        "Inserted %d new tables into metadata repository",
        table_inserts,
    )

    return table_inserts


# ---------------------------------------------------------------------------
# Table lookup
# ---------------------------------------------------------------------------

def build_table_lookup(
    cursor: pyodbc.Cursor,
    database_id: int,
) -> dict[tuple[str, str], int]:

    cursor.execute(
        """
        SELECT
            TableID,
            SchemaName,
            TableName
        FROM MetadataTable
        WHERE DatabaseID = ?
        """,
        database_id,
    )

    return {
        (schema_name, table_name): table_id
        for table_id, schema_name, table_name
        in cursor.fetchall()
    }


# ---------------------------------------------------------------------------
# Load columns
# ---------------------------------------------------------------------------

def load_columns(
    cursor: pyodbc.Cursor,
    database_id: int,
    columns: pd.DataFrame,
) -> int:

    table_lookup = build_table_lookup(
        cursor,
        database_id,
    )

    existing_columns = {}

    cursor.execute(
        """
        SELECT
            c.ColumnID,
            t.SchemaName,
            t.TableName,
            c.ColumnName
        FROM MetadataColumn c
        INNER JOIN MetadataTable t
            ON c.TableID = t.TableID
        WHERE t.DatabaseID = ?
        """,
        database_id,
    )

    for (
        column_id,
        schema_name,
        table_name,
        column_name,
    ) in cursor.fetchall():

        existing_columns[
            (
                schema_name,
                table_name,
                column_name,
            )
        ] = column_id

    column_inserts = 0

    for _, row in columns.iterrows():

        key = (
            row["TABLE_SCHEMA"],
            row["TABLE_NAME"],
            row["COLUMN_NAME"],
        )

        table_key = (
            row["TABLE_SCHEMA"],
            row["TABLE_NAME"],
        )

        table_id = table_lookup.get(table_key)

        if not table_id or key in existing_columns:
            continue

        max_length = row["CHARACTER_MAXIMUM_LENGTH"]

        max_length = (
            None
            if pd.isna(max_length)
            else int(max_length)
        )

        cursor.execute(
            """
            INSERT INTO MetadataColumn
            (
                TableID,
                ColumnName,
                DataType,
                MaxLength,
                IsNullable
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            table_id,
            str(row["COLUMN_NAME"]),
            str(row["DATA_TYPE"]),
            max_length,
            str(row["IS_NULLABLE"]),
        )

        column_inserts += 1

    cursor.connection.commit()

    logging.info(
        "Inserted %d new columns into metadata repository",
        column_inserts,
    )

    return column_inserts


# ---------------------------------------------------------------------------
# Load relationships
# ---------------------------------------------------------------------------

def load_relationships(
    cursor: pyodbc.Cursor,
    database_id: int,
    relationships: pd.DataFrame,
) -> int:

    table_lookup = build_table_lookup(
        cursor,
        database_id,
    )

    # Existing relationships are identified by
    # constraint name + database.
    cursor.execute(
        """
        SELECT
            r.RelationshipID,
            r.ConstraintName
        FROM MetadataRelationship r
        WHERE r.DatabaseID = ?
        """,
        database_id,
    )

    existing_relationships = {
        constraint_name: relationship_id
        for relationship_id, constraint_name
        in cursor.fetchall()
        if constraint_name is not None
    }

    # Metadata column lookup
    column_lookup = {}

    cursor.execute(
        """
        SELECT
            c.ColumnID,
            t.SchemaName,
            t.TableName,
            c.ColumnName
        FROM MetadataColumn c
        INNER JOIN MetadataTable t
            ON c.TableID = t.TableID
        WHERE t.DatabaseID = ?
        """,
        database_id,
    )

    for (
        column_id,
        schema_name,
        table_name,
        column_name,
    ) in cursor.fetchall():

        column_lookup[
            (
                schema_name,
                table_name,
                column_name,
            )
        ] = column_id

    relationship_inserts = 0
    relationship_column_inserts = 0

    for _, row in relationships.iterrows():

        constraint_name = row["CONSTRAINT_NAME"]

        parent_table_id = table_lookup.get(
            (
                row["PARENT_SCHEMA"],
                row["PARENT_TABLE"],
            )
        )

        child_table_id = table_lookup.get(
            (
                row["CHILD_SCHEMA"],
                row["CHILD_TABLE"],
            )
        )

        if not parent_table_id or not child_table_id:

            logging.warning(
                "Could not find tables for relationship %s",
                constraint_name,
            )

            continue

# ---------------------------------------------------------------
# Create or retrieve relationship
# ---------------------------------------------------------------

        relationship_id = existing_relationships.get(
            constraint_name
        )

        if relationship_id is None:

            cursor.execute(
                """
                INSERT INTO MetadataRelationship
             (
                 DatabaseID,
                    ParentTableID,
                    ParentColumnID,
                    ChildTableID,
                    ChildColumnID,
                    ConstraintName
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                database_id,
                parent_table_id,
                None,
                child_table_id,
                None,
                constraint_name,
            )

            cursor.connection.commit()

            cursor.execute(
                """
                SELECT MAX(RelationshipID)
                FROM MetadataRelationship
             WHERE DatabaseID = ?
               AND ConstraintName = ?
             """,
             database_id,
                constraint_name,
            )

            relationship_result = cursor.fetchone()

            if not relationship_result or relationship_result[0] is None:
                raise RuntimeError(
                  f"Failed to retrieve RelationshipID for constraint "
                 f"{constraint_name}."
                )

            relationship_id = int(relationship_result[0])

            existing_relationships[
                constraint_name
            ] = relationship_id

            relationship_inserts += 1

# ---------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------

        parent_column = str(
            row["PARENT_COLUMN"]
        ).strip()

        child_column = str(
            row["CHILD_COLUMN"]
        ).strip()

        parent_column_id = column_lookup.get(
            (
                row["PARENT_SCHEMA"],
                row["PARENT_TABLE"],
                parent_column,
            )
        )

        child_column_id = column_lookup.get(
            (
                row["CHILD_SCHEMA"],
                row["CHILD_TABLE"],
                child_column,
            )
        )

        if not parent_column_id or not child_column_id:

            logging.warning(
                "Could not find columns for relationship %s: %s -> %s",
                constraint_name,
                parent_column,
                child_column,
            )

            continue

        # Determine ordinal based on the source FK metadata.
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM MetadataRelationshipColumn
            WHERE RelationshipID = ?
            """,
            relationship_id,
        )

        ordinal = cursor.fetchone()[0] + 1

        # Avoid duplicate mapping.
        cursor.execute(
            """
            SELECT 1
            FROM MetadataRelationshipColumn
            WHERE RelationshipID = ?
              AND ParentColumnID = ?
              AND ChildColumnID = ?
            """,
            relationship_id,
            parent_column_id,
            child_column_id,
        )

        if cursor.fetchone():
            continue

        cursor.execute(
            """
            INSERT INTO MetadataRelationshipColumn
            (
                RelationshipID,
                ParentColumnID,
                ChildColumnID,
                ColumnOrdinal
            )
            VALUES (?, ?, ?, ?)
            """,
            relationship_id,
            parent_column_id,
            child_column_id,
            ordinal,
        )

        relationship_column_inserts += 1

    cursor.connection.commit()

    logging.info(
        "Inserted %d new relationships into metadata repository",
        relationship_inserts,
    )

    logging.info(
        "Inserted %d new relationship column mappings into metadata repository",
        relationship_column_inserts,
    )

    return relationship_inserts


# ---------------------------------------------------------------------------
# Write AI-friendly relationship CSV
# ---------------------------------------------------------------------------

def write_ai_relationships_csv(
    repo_conn: pyodbc.Connection,
    database_id: int,
    output_dir: Path,
) -> None:

    query = """
    SELECT
        r.ConstraintName AS ConstraintName,

        pt.SchemaName AS ParentSchema,
        pt.TableName AS ParentTable,

        ct.SchemaName AS ChildSchema,
        ct.TableName AS ChildTable,

        pc.ColumnName AS ParentColumn,
        cc.ColumnName AS ChildColumn,

        rc.ColumnOrdinal AS ColumnOrdinal

    FROM dbo.MetadataRelationship r

    INNER JOIN dbo.MetadataTable pt
        ON r.ParentTableID = pt.TableID

    INNER JOIN dbo.MetadataTable ct
        ON r.ChildTableID = ct.TableID

    INNER JOIN dbo.MetadataRelationshipColumn rc
        ON r.RelationshipID = rc.RelationshipID

    INNER JOIN dbo.MetadataColumn pc
        ON rc.ParentColumnID = pc.ColumnID

    INNER JOIN dbo.MetadataColumn cc
        ON rc.ChildColumnID = cc.ColumnID

    WHERE r.DatabaseID = ?

    ORDER BY
        pt.SchemaName,
        pt.TableName,
        r.ConstraintName,
        rc.ColumnOrdinal;
    """

    relationships = pd.read_sql(
        query,
        repo_conn,
        params=[database_id],
    )

    output_path = output_dir / "metadata_relationships_ai.csv"

    relationships.to_csv(
        output_path,
        index=False,
    )

    logging.info(
        "Wrote AI-friendly relationship CSV: %s",
        output_path,
    )


# ---------------------------------------------------------------------------
# Write metadata CSV
# ---------------------------------------------------------------------------

def write_metadata_csv(
    output_dir: Path,
    filename: str,
    dataframe: pd.DataFrame,
) -> None:

    destination = output_dir / filename

    dataframe.to_csv(
        destination,
        index=False,
    )

    logging.info(
        "Wrote metadata CSV: %s",
        destination,
    )


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "Extract metadata from SQL Server "
            "and load it into a Fabric Warehouse metadata repository."
        )
    )

    parser.add_argument(
        "--server",
        default=DEFAULT_SERVER,
        help="SQL Server source name",
    )

    parser.add_argument(
        "--source-database",
        default=DEFAULT_SOURCE_DATABASE,
        help="Source database name",
    )

    parser.add_argument(
        "--repository-database",
        default=DEFAULT_REPOSITORY_DATABASE,
        help="Fabric Warehouse database name",
    )

    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for output CSV files",
    )

    parser.add_argument(
        "--driver",
        default=DEFAULT_DRIVER,
        help="ODBC driver for SQL Server",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:

    args = parse_arguments()

    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
    )

    output_dir = Path(args.output_dir)

    ensure_output_dir(output_dir)

    source_conn = None
    repo_conn = None
    extraction_id = None

    try:

        # ---------------------------------------------------------------
        # Connect to source database
        # ---------------------------------------------------------------

        source_conn = connect_to_source(
            args.driver,
            args.server,
            args.source_database,
        )

        logging.info(
            "Connected to source database %s on server %s",
            args.source_database,
            args.server,
        )

        # ---------------------------------------------------------------
        # Connect to Fabric Warehouse
        # ---------------------------------------------------------------

        repo_conn = connect_to_fabric_warehouse(
            args.driver,
            FABRIC_SQL_SERVER,
            args.repository_database,
        )

        logging.info(
            "Connected to Fabric Warehouse %s",
            args.repository_database,
        )

        repo_cursor = repo_conn.cursor()

        # ---------------------------------------------------------------
        # Get database ID
        # ---------------------------------------------------------------

        database_id = get_database_id(
            repo_cursor,
            args.source_database,
            args.server,
        )

        if database_id is None:

            logging.info(
                "Source database not found in repository; "
                "inserting record"
            )

            database_id = insert_database(
                repo_cursor,
                args.source_database,
                args.server,
            )

        logging.info(
            "Using DatabaseID %s",
            database_id,
        )

        # ---------------------------------------------------------------
        # Start extraction log
        # ---------------------------------------------------------------

        extraction_id = start_extraction_log(
            repo_cursor,
            database_id,
        )

        logging.info(
            "Started extraction log %s",
            extraction_id,
        )

        # ---------------------------------------------------------------
        # Tables
        # ---------------------------------------------------------------

        tables = extract_tables(
            source_conn
        )

        tables_loaded = load_tables(
            repo_cursor,
            database_id,
            tables,
        )

        write_metadata_csv(
            output_dir,
            "tables_metadata.csv",
            tables,
        )

        # ---------------------------------------------------------------
        # Columns
        # ---------------------------------------------------------------

        columns = extract_columns(
            source_conn
        )

        columns_loaded = load_columns(
            repo_cursor,
            database_id,
            columns,
        )

        write_metadata_csv(
            output_dir,
            "columns_metadata.csv",
            columns,
        )

        # ---------------------------------------------------------------
        # Relationships
        # ---------------------------------------------------------------

        relationships = extract_relationships(
            source_conn
        )

        relationships_loaded = load_relationships(
            repo_cursor,
            database_id,
            relationships,
        )

        write_metadata_csv(
            output_dir,
            "relationships_metadata.csv",
            relationships,
        )

        # ---------------------------------------------------------------
        # AI-friendly relationship CSV
        # ---------------------------------------------------------------

        write_ai_relationships_csv(
            repo_conn,
            database_id,
            output_dir,
        )

        # ---------------------------------------------------------------
        # Mark extraction as successful
        # ---------------------------------------------------------------

        update_extraction_log(
            repo_cursor,
            extraction_id,
            "Success",
            tables_loaded=tables_loaded,
            columns_loaded=columns_loaded,
            relationships_loaded=relationships_loaded,
        )

        logging.info(
            "Extraction completed successfully"
        )

        return 0

    except Exception as exc:

        logging.exception(
            "Extraction failed"
        )

        if extraction_id and repo_conn:

            try:
                update_extraction_log(
                    repo_conn.cursor(),
                    extraction_id,
                    "Failed",
                    error_message=str(exc),
                )
            except Exception:
                logging.exception(
                    "Failed to update extraction log"
                )

        return 1

    finally:

        if source_conn:

            source_conn.close()

            logging.info(
                "Closed source database connection"
            )

        if repo_conn:

            repo_conn.close()

            logging.info(
                "Closed Fabric Warehouse connection"
            )


if __name__ == "__main__":
    raise SystemExit(main())

