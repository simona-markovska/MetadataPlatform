import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyodbc

# ---------------------------------------------------------------------------
# PROJECT ROOT
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from config.config import (
    DEFAULT_DRIVER,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SERVER,
    DEFAULT_SOURCE_DATABASE,
)


# ===========================================================================
# CONFIGURATION
# ===========================================================================

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

# ---------------------------------------------------------------------------
# METADATA REPOSITORY
# ---------------------------------------------------------------------------

# Fabric Warehouse SQL endpoint
FABRIC_SQL_SERVER = (
    "j7mjaqg22d2ujb27llpciiyism-7jnw46tiqcde5cpv233ctk345u.datawarehouse.fabric.microsoft.com"
)

# Fabric Warehouse database name
FABRIC_SQL_DATABASE = "MetadataRepository"


# ---------------------------------------------------------------------------
# Source system identifier
# ---------------------------------------------------------------------------

SOURCE_SYSTEM = "SQLSERVER"


# ---------------------------------------------------------------------------
# Batch size
#
# Fabric Warehouse is accessed over a network connection. We therefore
# avoid issuing one transaction per row.
# ---------------------------------------------------------------------------

BATCH_SIZE = 250


# ===========================================================================
# LOGGING
# ===========================================================================


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
    )


# ===========================================================================
# GENERAL UTILITIES
# ===========================================================================


def ensure_output_dir(path: Path) -> None:
    path.mkdir(
        parents=True,
        exist_ok=True,
    )


def clean_value(value) -> str:
    """
    Convert a value to a clean string.

    This is used for stable source keys.
    """

    if value is None:
        return ""

    if pd.isna(value):
        return ""

    return str(value).strip()


def nullable_int(value):
    """
    Convert pandas numeric/null values to Python int/None.
    """

    if value is None:
        return None

    if pd.isna(value):
        return None

    return int(value)


# ===========================================================================
# SOURCE CONNECTION
# ===========================================================================


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

    logging.info(
        "Connecting to source SQL Server %s / %s",
        server,
        database,
    )

    connection = pyodbc.connect(
        connection_string,
    )

    logging.info(
        "Source connection successful."
    )

    return connection


# ===========================================================================
# FABRIC WAREHOUSE CONNECTION
# ===========================================================================


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

    logging.info(
        "Connecting to Fabric Warehouse %s / %s",
        server,
        database,
    )

    connection = pyodbc.connect(
        connection_string,
    )

    logging.info(
        "Repository connection successful."
    )

    return connection


# ===========================================================================
# STABLE SOURCE KEYS
# ===========================================================================


def build_source_object_key(
    source_system: str,
    server_name: str,
    database_name: str,
    schema_name: str,
    table_name: str,
) -> str:

    return "|".join(
        [
            clean_value(source_system),
            clean_value(server_name),
            clean_value(database_name),
            clean_value(schema_name),
            clean_value(table_name),
        ]
    )


def build_source_column_key(
    source_system: str,
    server_name: str,
    database_name: str,
    schema_name: str,
    table_name: str,
    column_name: str,
) -> str:

    return "|".join(
        [
            clean_value(source_system),
            clean_value(server_name),
            clean_value(database_name),
            clean_value(schema_name),
            clean_value(table_name),
            clean_value(column_name),
        ]
    )


# ===========================================================================
# DATABASE METADATA
# ===========================================================================


def get_database_id(
    cursor: pyodbc.Cursor,
    database_name: str,
    server_name: str,
) -> int | None:

    cursor.execute(
        """
        SELECT DatabaseID
        FROM dbo.MetadataDatabase
        WHERE DatabaseName = ?
          AND ServerName = ?
        """,
        database_name,
        server_name,
    )

    result = cursor.fetchone()

    if result is None:
        return None

    return int(result[0])


def create_database(
    cursor: pyodbc.Cursor,
    database_name: str,
    server_name: str,
) -> int:

    source_system = SOURCE_SYSTEM

    cursor.execute(
        """
        INSERT INTO dbo.MetadataDatabase
        (
            DatabaseName,
            ServerName,
            SourceSystem
        )
        VALUES (?, ?, ?)
        """,
        database_name,
        server_name,
        source_system,
    )

    cursor.connection.commit()

    database_id = get_database_id(
        cursor,
        database_name,
        server_name,
    )

    if database_id is None:
        raise RuntimeError(
            "Database was inserted but DatabaseID could not be resolved."
        )

    logging.info(
        "Created DatabaseID=%s",
        database_id,
    )

    return database_id


def get_or_create_database(
    cursor: pyodbc.Cursor,
    database_name: str,
    server_name: str,
) -> int:

    database_id = get_database_id(
        cursor,
        database_name,
        server_name,
    )

    if database_id is not None:
        logging.info(
            "Existing DatabaseID=%s found.",
            database_id,
        )

        return database_id

    return create_database(
        cursor,
        database_name,
        server_name,
    )


# ===========================================================================
# SOURCE EXTRACTION
# ===========================================================================


def extract_tables(
    source_conn: pyodbc.Connection,
) -> pd.DataFrame:

    query = """
    SELECT
        TABLE_SCHEMA,
        TABLE_NAME,
        TABLE_TYPE
    FROM INFORMATION_SCHEMA.TABLES
    ORDER BY
        TABLE_SCHEMA,
        TABLE_NAME;
    """

    cursor = source_conn.cursor()

    cursor.execute(query)

    rows = cursor.fetchall()

    columns = [
        "TABLE_SCHEMA",
        "TABLE_NAME",
        "TABLE_TYPE",
    ]

    tables = pd.DataFrame.from_records(
        rows,
        columns=columns,
    )

    logging.info(
        "Extracted %d source objects",
        len(tables),
    )

    return tables


def extract_columns(
    source_conn: pyodbc.Connection,
) -> pd.DataFrame:

    query = """
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

    cursor = source_conn.cursor()

    cursor.execute(query)

    rows = cursor.fetchall()

    columns = [
        "TABLE_SCHEMA",
        "TABLE_NAME",
        "ORDINAL_POSITION",
        "COLUMN_NAME",
        "DATA_TYPE",
        "CHARACTER_MAXIMUM_LENGTH",
        "IS_NULLABLE",
    ]

    dataframe = pd.DataFrame.from_records(
        rows,
        columns=columns,
    )

    logging.info(
        "Extracted %d source columns",
        len(dataframe),
    )

    return dataframe


def extract_relationships(
    source_conn: pyodbc.Connection,
) -> pd.DataFrame:

    query = """
    SELECT
        fk.name AS CONSTRAINT_NAME,

        parent_schema.name AS PARENT_SCHEMA,
        parent_table.name AS PARENT_TABLE,
        parent_column.name AS PARENT_COLUMN,

        child_schema.name AS CHILD_SCHEMA,
        child_table.name AS CHILD_TABLE,
        child_column.name AS CHILD_COLUMN,

        fkc.constraint_column_id AS COLUMN_ORDINAL

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

    cursor = source_conn.cursor()

    cursor.execute(query)

    rows = cursor.fetchall()

    columns = [
        "CONSTRAINT_NAME",
        "PARENT_SCHEMA",
        "PARENT_TABLE",
        "PARENT_COLUMN",
        "CHILD_SCHEMA",
        "CHILD_TABLE",
        "CHILD_COLUMN",
        "COLUMN_ORDINAL",
    ]

    relationships = pd.DataFrame.from_records(
        rows,
        columns=columns,
    )

    logging.info(
        "Extracted %d FK column mappings",
        len(relationships),
    )

    return relationships


# ===========================================================================
# ADD STABLE SOURCE KEYS
# ===========================================================================


def enrich_tables_with_source_keys(
    tables: pd.DataFrame,
    server_name: str,
    database_name: str,
) -> pd.DataFrame:

    tables = tables.copy()

    tables["SourceObjectKey"] = tables.apply(
        lambda row: build_source_object_key(
            SOURCE_SYSTEM,
            server_name,
            database_name,
            row["TABLE_SCHEMA"],
            row["TABLE_NAME"],
        ),
        axis=1,
    )

    return tables


def enrich_columns_with_source_keys(
    columns: pd.DataFrame,
    server_name: str,
    database_name: str,
) -> pd.DataFrame:

    columns = columns.copy()

    columns["SourceColumnKey"] = columns.apply(
        lambda row: build_source_column_key(
            SOURCE_SYSTEM,
            server_name,
            database_name,
            row["TABLE_SCHEMA"],
            row["TABLE_NAME"],
            row["COLUMN_NAME"],
        ),
        axis=1,
    )

    return columns


# ===========================================================================
# EXISTING REPOSITORY LOOKUPS
# ===========================================================================


def load_existing_tables(
    cursor: pyodbc.Cursor,
    database_id: int,
) -> dict[str, int]:

    cursor.execute(
        """
        SELECT
            TableID,
            SourceObjectKey
        FROM dbo.MetadataTable
        WHERE DatabaseID = ?
          AND SourceObjectKey IS NOT NULL
        """,
        database_id,
    )

    lookup = {}

    for table_id, source_object_key in cursor.fetchall():

        lookup[str(source_object_key)] = int(table_id)

    return lookup


def load_existing_columns(
    cursor: pyodbc.Cursor,
    database_id: int,
) -> dict[str, int]:

    cursor.execute(
        """
        SELECT
            c.ColumnID,
            c.SourceColumnKey
        FROM dbo.MetadataColumn c
        INNER JOIN dbo.MetadataTable t
            ON c.TableID = t.TableID
        WHERE t.DatabaseID = ?
          AND c.SourceColumnKey IS NOT NULL
        """,
        database_id,
    )

    lookup = {}

    for column_id, source_column_key in cursor.fetchall():

        lookup[str(source_column_key)] = int(column_id)

    return lookup


# ===========================================================================
# LOAD TABLES
# ===========================================================================


def load_tables(
    cursor: pyodbc.Cursor,
    database_id: int,
    tables: pd.DataFrame,
    server_name: str,
    database_name: str,
) -> tuple[dict[str, int], int, int]:

    logging.info(
        "Loading source tables..."
    )

    existing = load_existing_tables(
        cursor,
        database_id,
    )

    inserted = 0
    updated = 0

    table_lookup = dict(existing)

    pending_inserts = []

    for _, row in tables.iterrows():

        source_object_key = clean_value(
            row["SourceObjectKey"]
        )

        schema_name = clean_value(
            row["TABLE_SCHEMA"]
        )

        table_name = clean_value(
            row["TABLE_NAME"]
        )

        table_type = clean_value(
            row["TABLE_TYPE"]
        )

        if source_object_key in existing:

            table_id = existing[source_object_key]

            cursor.execute(
                """
                UPDATE dbo.MetadataTable
                SET
                    SchemaName = ?,
                    TableName = ?,
                    TableType = ?
                WHERE TableID = ?
                """,
                schema_name,
                table_name,
                table_type,
                table_id,
            )

            updated += 1

        else:

            pending_inserts.append(
                (
                    database_id,
                    schema_name,
                    table_name,
                    table_type,
                    source_object_key,
                )
            )

    # -----------------------------------------------------------------------
    # Batch inserts
    # -----------------------------------------------------------------------

    for start in range(
        0,
        len(pending_inserts),
        BATCH_SIZE,
    ):

        batch = pending_inserts[
            start:start + BATCH_SIZE
        ]

        for row in batch:

            cursor.execute(
                """
                INSERT INTO dbo.MetadataTable
                (
                    DatabaseID,
                    SchemaName,
                    TableName,
                    TableType,
                    SourceObjectKey
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                *row,
            )

        cursor.connection.commit()

    inserted = len(pending_inserts)

    # -----------------------------------------------------------------------
    # Rebuild lookup once after inserts
    # -----------------------------------------------------------------------

    table_lookup = load_existing_tables(
        cursor,
        database_id,
    )

    logging.info(
        "Tables: inserted=%d updated=%d total=%d",
        inserted,
        updated,
        len(table_lookup),
    )

    return (
        table_lookup,
        inserted,
        updated,
    )


# ===========================================================================
# LOAD COLUMNS
# ===========================================================================


def load_columns(
    cursor: pyodbc.Cursor,
    database_id: int,
    columns: pd.DataFrame,
    table_lookup: dict[str, int],
    server_name: str,
    database_name: str,
) -> tuple[dict[str, int], int, int, int]:

    logging.info(
        "Loading source columns..."
    )

    existing = load_existing_columns(
        cursor,
        database_id,
    )

    column_lookup = dict(existing)

    inserted = 0
    updated = 0
    unresolved_tables = 0

    pending_inserts = []

    for _, row in columns.iterrows():

        source_object_key = build_source_object_key(
            SOURCE_SYSTEM,
            server_name,
            database_name,
            row["TABLE_SCHEMA"],
            row["TABLE_NAME"],
        )

        table_id = table_lookup.get(
            source_object_key
        )

        if table_id is None:

            unresolved_tables += 1

            logging.warning(
                "Cannot resolve table for column %s.%s.%s",
                clean_value(row["TABLE_SCHEMA"]),
                clean_value(row["TABLE_NAME"]),
                clean_value(row["COLUMN_NAME"]),
            )

            continue

        source_column_key = clean_value(
            row["SourceColumnKey"]
        )

        column_name = clean_value(
            row["COLUMN_NAME"]
        )

        data_type = clean_value(
            row["DATA_TYPE"]
        )

        max_length = nullable_int(
            row["CHARACTER_MAXIMUM_LENGTH"]
        )

        is_nullable = clean_value(
            row["IS_NULLABLE"]
        )

        if source_column_key in existing:

            column_id = existing[source_column_key]

            cursor.execute(
                """
                UPDATE dbo.MetadataColumn
                SET
                    TableID = ?,
                    ColumnName = ?,
                    DataType = ?,
                    MaxLength = ?,
                    IsNullable = ?
                WHERE ColumnID = ?
                """,
                table_id,
                column_name,
                data_type,
                max_length,
                is_nullable,
                column_id,
            )

            updated += 1

        else:

            pending_inserts.append(
                (
                    table_id,
                    column_name,
                    data_type,
                    max_length,
                    is_nullable,
                    source_column_key,
                )
            )

    # -----------------------------------------------------------------------
    # Batch insert columns
    # -----------------------------------------------------------------------

    for start in range(
        0,
        len(pending_inserts),
        BATCH_SIZE,
    ):

        batch = pending_inserts[
            start:start + BATCH_SIZE
        ]

        for row in batch:

            cursor.execute(
                """
                INSERT INTO dbo.MetadataColumn
                (
                    TableID,
                    ColumnName,
                    DataType,
                    MaxLength,
                    IsNullable,
                    SourceColumnKey
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                *row,
            )

        cursor.connection.commit()

    inserted = len(pending_inserts)

    # -----------------------------------------------------------------------
    # Reload complete column lookup
    # -----------------------------------------------------------------------

    column_lookup = load_existing_columns(
        cursor,
        database_id,
    )

    logging.info(
        "Columns: inserted=%d updated=%d total=%d unresolved_tables=%d",
        inserted,
        updated,
        len(column_lookup),
        unresolved_tables,
    )

    return (
        column_lookup,
        inserted,
        updated,
        unresolved_tables,
    )


# ===========================================================================
# RELATIONSHIP LOOKUPS
# ===========================================================================


def load_existing_relationships(
    cursor: pyodbc.Cursor,
    database_id: int,
) -> dict[tuple[str, int, int], int]:

    cursor.execute(
        """
        SELECT
            RelationshipID,
            ConstraintName,
            ParentTableID,
            ChildTableID
        FROM dbo.MetadataRelationship
        WHERE DatabaseID = ?
        """,
        database_id,
    )

    lookup = {}

    for (
        relationship_id,
        constraint_name,
        parent_table_id,
        child_table_id,
    ) in cursor.fetchall():

        key = (
            clean_value(constraint_name),
            int(parent_table_id),
            int(child_table_id),
        )

        lookup[key] = int(relationship_id)

    return lookup


def load_existing_relationship_columns(
    cursor: pyodbc.Cursor,
) -> set[tuple[int, int, int, int]]:

    cursor.execute(
        """
        SELECT
            RelationshipID,
            ParentColumnID,
            ChildColumnID,
            ColumnOrdinal
        FROM dbo.MetadataRelationshipColumn
        """
    )

    return {
        (
            int(relationship_id),
            int(parent_column_id),
            int(child_column_id),
            int(column_ordinal),
        )
        for (
            relationship_id,
            parent_column_id,
            child_column_id,
            column_ordinal,
        ) in cursor.fetchall()
    }


# ===========================================================================
# LOAD RELATIONSHIPS
# ===========================================================================


def load_relationships(
    cursor: pyodbc.Cursor,
    database_id: int,
    relationships: pd.DataFrame,
    table_lookup: dict[str, int],
    column_lookup: dict[str, int],
    server_name: str,
    database_name: str,
) -> tuple[int, int, int]:

    logging.info(
        "Loading source relationships..."
    )

    existing_relationships = load_existing_relationships(
        cursor,
        database_id,
    )

    existing_relationship_columns = (
        load_existing_relationship_columns(
            cursor
        )
    )

    relationship_inserted = 0
    relationship_column_inserted = 0
    unresolved = 0

    relationship_lookup = dict(
        existing_relationships
    )

    for _, row in relationships.iterrows():

        parent_object_key = build_source_object_key(
            SOURCE_SYSTEM,
            server_name,
            database_name,
            row["PARENT_SCHEMA"],
            row["PARENT_TABLE"],
        )

        child_object_key = build_source_object_key(
            SOURCE_SYSTEM,
            server_name,
            database_name,
            row["CHILD_SCHEMA"],
            row["CHILD_TABLE"],
        )

        parent_table_id = table_lookup.get(
            parent_object_key
        )

        child_table_id = table_lookup.get(
            child_object_key
        )

        parent_column_key = build_source_column_key(
            SOURCE_SYSTEM,
            server_name,
            database_name,
            row["PARENT_SCHEMA"],
            row["PARENT_TABLE"],
            row["PARENT_COLUMN"],
        )

        child_column_key = build_source_column_key(
            SOURCE_SYSTEM,
            server_name,
            database_name,
            row["CHILD_SCHEMA"],
            row["CHILD_TABLE"],
            row["CHILD_COLUMN"],
        )

        parent_column_id = column_lookup.get(
            parent_column_key
        )

        child_column_id = column_lookup.get(
            child_column_key
        )

        if (
            parent_table_id is None
            or child_table_id is None
            or parent_column_id is None
            or child_column_id is None
        ):

            unresolved += 1

            logging.warning(
                "Unresolved relationship: %s.%s -> %s.%s",
                clean_value(row["PARENT_TABLE"]),
                clean_value(row["PARENT_COLUMN"]),
                clean_value(row["CHILD_TABLE"]),
                clean_value(row["CHILD_COLUMN"]),
            )

            continue

        constraint_name = clean_value(
            row["CONSTRAINT_NAME"]
        )

        relationship_key = (
            constraint_name,
            parent_table_id,
            child_table_id,
        )

        relationship_id = relationship_lookup.get(
            relationship_key
        )

        # -------------------------------------------------------------------
        # Create relationship if necessary
        # -------------------------------------------------------------------

        if relationship_id is None:

            cursor.execute(
                """
                INSERT INTO dbo.MetadataRelationship
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
                parent_column_id,
                child_table_id,
                child_column_id,
                constraint_name,
            )

            cursor.connection.commit()

            # Resolve ID using deterministic relationship attributes.
            cursor.execute(
                """
                SELECT RelationshipID
                FROM dbo.MetadataRelationship
                WHERE DatabaseID = ?
                  AND ConstraintName = ?
                  AND ParentTableID = ?
                  AND ChildTableID = ?
                ORDER BY RelationshipID DESC
                """,
                database_id,
                constraint_name,
                parent_table_id,
                child_table_id,
            )

            result = cursor.fetchone()

            if result is None:

                raise RuntimeError(
                    "Could not resolve newly inserted "
                    f"relationship {constraint_name}."
                )

            relationship_id = int(
                result[0]
            )

            relationship_lookup[
                relationship_key
            ] = relationship_id

            relationship_inserted += 1

        # -------------------------------------------------------------------
        # Relationship column mapping
        # -------------------------------------------------------------------

        ordinal = int(
            row["COLUMN_ORDINAL"]
        )

        relationship_column_key = (
            relationship_id,
            parent_column_id,
            child_column_id,
            ordinal,
        )

        if (
            relationship_column_key
            in existing_relationship_columns
        ):
            continue

        cursor.execute(
            """
            INSERT INTO dbo.MetadataRelationshipColumn
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

        existing_relationship_columns.add(
            relationship_column_key
        )

        relationship_column_inserted += 1

    cursor.connection.commit()

    logging.info(
        "Relationships: inserted=%d",
        relationship_inserted,
    )

    logging.info(
        "Relationship column mappings: inserted=%d",
        relationship_column_inserted,
    )

    logging.info(
        "Unresolved relationship mappings: %d",
        unresolved,
    )

    return (
        relationship_inserted,
        relationship_column_inserted,
        unresolved,
    )


# ===========================================================================
# VALIDATION
# ===========================================================================


def validate_repository(
    cursor: pyodbc.Cursor,
    database_id: int,
    expected_tables: int,
    expected_columns: int,
    expected_relationship_columns: int,
) -> dict:

    logging.info(
        ""
    )

    logging.info(
        "=" * 70
    )

    logging.info(
        "V6 SQL REPOSITORY VALIDATION"
    )

    logging.info(
        "=" * 70
    )

    # -----------------------------------------------------------------------
    # Table count
    # -----------------------------------------------------------------------

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM dbo.MetadataTable
        WHERE DatabaseID = ?
        """,
        database_id,
    )

    table_count = int(
        cursor.fetchone()[0]
    )

    # -----------------------------------------------------------------------
    # Column count
    # -----------------------------------------------------------------------

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM dbo.MetadataColumn c
        INNER JOIN dbo.MetadataTable t
            ON c.TableID = t.TableID
        WHERE t.DatabaseID = ?
        """,
        database_id,
    )

    column_count = int(
        cursor.fetchone()[0]
    )

    # -----------------------------------------------------------------------
    # Relationship count
    # -----------------------------------------------------------------------

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM dbo.MetadataRelationship
        WHERE DatabaseID = ?
        """,
        database_id,
    )

    relationship_count = int(
        cursor.fetchone()[0]
    )

    # -----------------------------------------------------------------------
    # Relationship column count
    # -----------------------------------------------------------------------

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM dbo.MetadataRelationshipColumn rc
        INNER JOIN dbo.MetadataRelationship r
            ON rc.RelationshipID = r.RelationshipID
        WHERE r.DatabaseID = ?
        """,
        database_id,
    )

    relationship_column_count = int(
        cursor.fetchone()[0]
    )

    # -----------------------------------------------------------------------
    # Source object key coverage
    # -----------------------------------------------------------------------

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM dbo.MetadataTable
        WHERE DatabaseID = ?
          AND SourceObjectKey IS NOT NULL
          AND SourceObjectKey <> ''
        """,
        database_id,
    )

    tables_with_keys = int(
        cursor.fetchone()[0]
    )

    # -----------------------------------------------------------------------
    # Source column key coverage
    # -----------------------------------------------------------------------

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM dbo.MetadataColumn c
        INNER JOIN dbo.MetadataTable t
            ON c.TableID = t.TableID
        WHERE t.DatabaseID = ?
          AND c.SourceColumnKey IS NOT NULL
          AND c.SourceColumnKey <> ''
        """,
        database_id,
    )

    columns_with_keys = int(
        cursor.fetchone()[0]
    )

    # -----------------------------------------------------------------------
    # Duplicate source object keys
    # -----------------------------------------------------------------------

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM
        (
            SELECT SourceObjectKey
            FROM dbo.MetadataTable
            WHERE DatabaseID = ?
              AND SourceObjectKey IS NOT NULL
            GROUP BY SourceObjectKey
            HAVING COUNT(*) > 1
        ) d
        """,
        database_id,
    )

    duplicate_object_keys = int(
        cursor.fetchone()[0]
    )

    # -----------------------------------------------------------------------
    # Duplicate source column keys
    # -----------------------------------------------------------------------

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM
        (
            SELECT c.SourceColumnKey
            FROM dbo.MetadataColumn c
            INNER JOIN dbo.MetadataTable t
                ON c.TableID = t.TableID
            WHERE t.DatabaseID = ?
              AND c.SourceColumnKey IS NOT NULL
            GROUP BY c.SourceColumnKey
            HAVING COUNT(*) > 1
        ) d
        """,
        database_id,
    )

    duplicate_column_keys = int(
        cursor.fetchone()[0]
    )

    # -----------------------------------------------------------------------
    # Broken table references
    # -----------------------------------------------------------------------

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM dbo.MetadataRelationship r
        LEFT JOIN dbo.MetadataTable pt
            ON r.ParentTableID = pt.TableID
        LEFT JOIN dbo.MetadataTable ct
            ON r.ChildTableID = ct.TableID
        WHERE r.DatabaseID = ?
          AND
          (
              pt.TableID IS NULL
              OR ct.TableID IS NULL
          )
        """,
        database_id,
    )

    broken_relationships = int(
        cursor.fetchone()[0]
    )

    # -----------------------------------------------------------------------
    # Broken column references
    # -----------------------------------------------------------------------

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM dbo.MetadataRelationshipColumn rc
        INNER JOIN dbo.MetadataRelationship r
            ON rc.RelationshipID = r.RelationshipID
        LEFT JOIN dbo.MetadataColumn pc
            ON rc.ParentColumnID = pc.ColumnID
        LEFT JOIN dbo.MetadataColumn cc
            ON rc.ChildColumnID = cc.ColumnID
        WHERE r.DatabaseID = ?
          AND
          (
              pc.ColumnID IS NULL
              OR cc.ColumnID IS NULL
          )
        """,
        database_id,
    )

    broken_relationship_columns = int(
        cursor.fetchone()[0]
    )

    # -----------------------------------------------------------------------
    # Expected counts
    # -----------------------------------------------------------------------

    counts_match = (
        table_count == expected_tables
        and column_count == expected_columns
        and relationship_column_count
        == expected_relationship_columns
    )

    key_coverage_valid = (
        tables_with_keys == table_count
        and columns_with_keys == column_count
    )

    references_valid = (
        broken_relationships == 0
        and broken_relationship_columns == 0
    )

    uniqueness_valid = (
        duplicate_object_keys == 0
        and duplicate_column_keys == 0
    )

    validation_passed = (
        counts_match
        and key_coverage_valid
        and references_valid
        and uniqueness_valid
    )

    # -----------------------------------------------------------------------
    # Output
    # -----------------------------------------------------------------------

    logging.info(
        "DatabaseID:                         %s",
        database_id,
    )

    logging.info(
        "Tables:                              %d",
        table_count,
    )

    logging.info(
        "Columns:                             %d",
        column_count,
    )

    logging.info(
        "Relationships:                       %d",
        relationship_count,
    )

    logging.info(
        "Relationship column mappings:        %d",
        relationship_column_count,
    )

    logging.info(
        "Expected source tables:              %d",
        expected_tables,
    )

    logging.info(
        "Expected source columns:             %d",
        expected_columns,
    )

    logging.info(
        "Expected FK mappings:                %d",
        expected_relationship_columns,
    )

    logging.info(
        "Tables with SourceObjectKey:          %d/%d",
        tables_with_keys,
        table_count,
    )

    logging.info(
        "Columns with SourceColumnKey:         %d/%d",
        columns_with_keys,
        column_count,
    )

    logging.info(
        "Duplicate SourceObjectKeys:           %d",
        duplicate_object_keys,
    )

    logging.info(
        "Duplicate SourceColumnKeys:           %d",
        duplicate_column_keys,
    )

    logging.info(
        "Broken relationship table refs:       %d",
        broken_relationships,
    )

    logging.info(
        "Broken relationship column refs:      %d",
        broken_relationship_columns,
    )

    logging.info(
        "Counts match source:                  %s",
        "YES" if counts_match else "NO",
    )

    logging.info(
        "Source key coverage valid:            %s",
        "YES" if key_coverage_valid else "NO",
    )

    logging.info(
        "Reference integrity valid:            %s",
        "YES" if references_valid else "NO",
    )

    logging.info(
        "Source key uniqueness valid:          %s",
        "YES" if uniqueness_valid else "NO",
    )

    logging.info(
        "=" * 70
    )

    if validation_passed:

        logging.info(
            "V6 SQL REPOSITORY VALIDATION: PASSED"
        )

    else:

        logging.error(
            "V6 SQL REPOSITORY VALIDATION: FAILED"
        )

    logging.info(
        "=" * 70
    )

    return {
        "passed": validation_passed,
        "tables": table_count,
        "columns": column_count,
        "relationships": relationship_count,
        "relationship_columns": relationship_column_count,
        "tables_with_keys": tables_with_keys,
        "columns_with_keys": columns_with_keys,
        "duplicate_object_keys": duplicate_object_keys,
        "duplicate_column_keys": duplicate_column_keys,
        "broken_relationships": broken_relationships,
        "broken_relationship_columns": broken_relationship_columns,
    }


# ===========================================================================
# CSV OUTPUT
# ===========================================================================


def write_csv(
    dataframe: pd.DataFrame,
    output_dir: Path,
    filename: str,
) -> None:

    path = output_dir / filename

    dataframe.to_csv(
        path,
        index=False,
    )

    logging.info(
        "Wrote %s",
        path,
    )


def write_ai_relationship_csv(
    repo_conn: pyodbc.Connection,
    database_id: int,
    output_dir: Path,
) -> None:

    query = """
    SELECT
        r.ConstraintName,

        pt.SchemaName AS ParentSchema,
        pt.TableName AS ParentTable,

        pc.ColumnName AS ParentColumn,

        ct.SchemaName AS ChildSchema,
        ct.TableName AS ChildTable,

        cc.ColumnName AS ChildColumn,

        rc.ColumnOrdinal,

        pt.SourceObjectKey AS ParentSourceObjectKey,
        ct.SourceObjectKey AS ChildSourceObjectKey,

        pc.SourceColumnKey AS ParentSourceColumnKey,
        cc.SourceColumnKey AS ChildSourceColumnKey

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

    cursor = repo_conn.cursor()

    cursor.execute(
        query,
        database_id,
    )

    rows = cursor.fetchall()

    columns = [
        "ConstraintName",
        "ParentSchema",
        "ParentTable",
        "ParentColumn",
        "ChildSchema",
        "ChildTable",
        "ChildColumn",
        "ColumnOrdinal",
        "ParentSourceObjectKey",
        "ChildSourceObjectKey",
        "ParentSourceColumnKey",
        "ChildSourceColumnKey",
    ]

    dataframe = pd.DataFrame.from_records(
        rows,
        columns=columns,
    )

    write_csv(
        dataframe,
        output_dir,
        "metadata_relationships_ai_v6.csv",
    )


# ===========================================================================
# ARGUMENTS
# ===========================================================================


def parse_arguments() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "V6 reusable SQL Server metadata extractor "
            "for Fabric Warehouse."
        )
    )

    parser.add_argument(
        "--server",
        default=DEFAULT_SERVER,
        help="Source SQL Server name.",
    )

    parser.add_argument(
        "--source-database",
        default=DEFAULT_SOURCE_DATABASE,
        help="Source SQL Server database.",
    )

    parser.add_argument(
        "--repository-database",
        default=FABRIC_SQL_DATABASE,
        help="Fabric Warehouse repository database.",
    )

    parser.add_argument(
        "--repository-server",
        default=FABRIC_SQL_SERVER,
        help="Fabric Warehouse SQL endpoint.",
    )

    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for CSV files.",
    )

    parser.add_argument(
        "--driver",
        default=DEFAULT_DRIVER,
        help="ODBC driver.",
    )

    parser.add_argument(
        "--source-system",
        default=SOURCE_SYSTEM,
        help="Logical source-system identifier.",
    )

    return parser.parse_args()


# ===========================================================================
# MAIN
# ===========================================================================


def main() -> int:

    configure_logging()

    args = parse_arguments()

    output_dir = Path(
        args.output_dir
    )

    ensure_output_dir(
        output_dir
    )

    source_conn = None
    repository_conn = None

    start_time = datetime.now()

    try:

        # ===================================================================
        # Connections
        # ===================================================================

        source_conn = connect_to_source(
            args.driver,
            args.server,
            args.source_database,
        )

        repository_conn = connect_to_fabric_warehouse(
            args.driver,
            args.repository_server,
            args.repository_database,
        )

        repo_cursor = repository_conn.cursor()

        # ===================================================================
        # Database
        # ===================================================================

        database_id = get_or_create_database(
            repo_cursor,
            args.source_database,
            args.server,
        )

        logging.info(
            "Using repository DatabaseID=%s",
            database_id,
        )

        # ===================================================================
        # Extract
        # ===================================================================

        tables = extract_tables(
            source_conn
        )

        columns = extract_columns(
            source_conn
        )

        relationships = extract_relationships(
            source_conn
        )

        # ===================================================================
        # Stable source keys
        # ===================================================================

        tables = enrich_tables_with_source_keys(
            tables,
            args.server,
            args.source_database,
        )

        columns = enrich_columns_with_source_keys(
            columns,
            args.server,
            args.source_database,
        )

        # ===================================================================
        # CSV extraction snapshots
        # ===================================================================

        write_csv(
            tables,
            output_dir,
            "tables_metadata_v6.csv",
        )

        write_csv(
            columns,
            output_dir,
            "columns_metadata_v6.csv",
        )

        write_csv(
            relationships,
            output_dir,
            "relationships_metadata_v6.csv",
        )

        # ===================================================================
        # Load tables
        # ===================================================================

        (
            table_lookup,
            tables_inserted,
            tables_updated,
        ) = load_tables(
            repo_cursor,
            database_id,
            tables,
            args.server,
            args.source_database,
        )

        # ===================================================================
        # Load columns
        # ===================================================================

        (
            column_lookup,
            columns_inserted,
            columns_updated,
            unresolved_column_tables,
        ) = load_columns(
            repo_cursor,
            database_id,
            columns,
            table_lookup,
            args.server,
            args.source_database,
        )

        # ===================================================================
        # Load relationships
        # ===================================================================

        (
            relationships_inserted,
            relationship_columns_inserted,
            unresolved_relationships,
        ) = load_relationships(
            repo_cursor,
            database_id,
            relationships,
            table_lookup,
            column_lookup,
            args.server,
            args.source_database,
        )

        # ===================================================================
        # AI relationship snapshot
        # ===================================================================

        write_ai_relationship_csv(
            repository_conn,
            database_id,
            output_dir,
        )

        # ===================================================================
        # Validation
        # ===================================================================

        validation = validate_repository(
            repo_cursor,
            database_id,
            expected_tables=len(tables),
            expected_columns=len(columns),
            expected_relationship_columns=len(
                relationships
            ),
        )

        # ===================================================================
        # Final summary
        # ===================================================================

        elapsed = (
            datetime.now()
            - start_time
        ).total_seconds()

        logging.info(
            ""
        )

        logging.info(
            "=" * 70
        )

        logging.info(
            "V6 SQL EXTRACTOR FINAL SUMMARY"
        )

        logging.info(
            "=" * 70
        )

        logging.info(
            "Source database:                 %s",
            args.source_database,
        )

        logging.info(
            "Source server:                   %s",
            args.server,
        )

        logging.info(
            "Repository:                      %s",
            args.repository_database,
        )

        logging.info(
            "DatabaseID:                      %s",
            database_id,
        )

        logging.info(
            ""
        )

        logging.info(
            "Source objects extracted:        %d",
            len(tables),
        )

        logging.info(
            "Source columns extracted:        %d",
            len(columns),
        )

        logging.info(
            "FK column mappings extracted:    %d",
            len(relationships),
        )

        logging.info(
            ""
        )

        logging.info(
            "Tables inserted:                 %d",
            tables_inserted,
        )

        logging.info(
            "Tables updated:                  %d",
            tables_updated,
        )

        logging.info(
            "Columns inserted:               %d",
            columns_inserted,
        )

        logging.info(
            "Columns updated:                %d",
            columns_updated,
        )

        logging.info(
            "Relationships inserted:        %d",
            relationships_inserted,
        )

        logging.info(
            "Relationship mappings inserted: %d",
            relationship_columns_inserted,
        )

        logging.info(
            ""
        )

        logging.info(
            "Unresolved column tables:        %d",
            unresolved_column_tables,
        )

        logging.info(
            "Unresolved relationships:       %d",
            unresolved_relationships,
        )

        logging.info(
            ""
        )

        logging.info(
            "Validation:                     %s",
            "PASSED" if validation["passed"]
            else "FAILED",
        )

        logging.info(
            "Elapsed time:                   %.2f seconds",
            elapsed,
        )

        logging.info(
            "=" * 70
        )

        if validation["passed"]:

            logging.info(
                "V6 SQL EXTRACTOR COMPLETED SUCCESSFULLY."
            )

            return 0

        logging.error(
            "V6 SQL EXTRACTOR COMPLETED WITH VALIDATION ERRORS."
        )

        return 1

    except KeyboardInterrupt:

        logging.error(
            "SQL EXTRACTOR V2 INTERRUPTED BY USER."
        )

        return 130

    except Exception:

        logging.exception(
            "SQL EXTRACTOR V2 FAILED."
        )

        return 1

    finally:

        if source_conn is not None:

            try:
                source_conn.close()

                logging.info(
                    "Closed source SQL connection."
                )

            except Exception:
                logging.exception(
                    "Error closing source connection."
                )

        if repository_conn is not None:

            try:
                repository_conn.close()

                logging.info(
                    "Closed Fabric Warehouse connection."
                )

            except Exception:
                logging.exception(
                    "Error closing repository connection."
                )


# ===========================================================================
# ENTRY POINT
# ===========================================================================


if __name__ == "__main__":
    raise SystemExit(
        main()
    )