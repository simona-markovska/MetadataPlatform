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


LOG_FORMAT = '%(asctime)s %(levelname)s %(message)s'


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def get_connection_string(driver: str, server: str, database: str) -> str:
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )


def connect_to_database(driver: str, server: str, database: str) -> pyodbc.Connection:
    connection_string = get_connection_string(driver, server, database)
    return pyodbc.connect(connection_string)


def get_database_id(cursor: pyodbc.Cursor, database_name: str, server_name: str) -> int | None:
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


def insert_database(cursor: pyodbc.Cursor, database_name: str, server_name: str) -> int:
    cursor.execute(
        """
        INSERT INTO MetadataDatabase
        (DatabaseName, ServerName)
        VALUES (?, ?)
        """,
        database_name,
        server_name,
    )
    cursor.connection.commit()
    return get_database_id(cursor, database_name, server_name)


def start_extraction_log(cursor: pyodbc.Cursor, database_id: int) -> int:
    start_time = datetime.now()
    cursor.execute(
        """
        INSERT INTO MetadataExtractionLog
        (DatabaseID, StartTime, Status)
        OUTPUT INSERTED.ExtractionID
        VALUES (?, ?, ?)
        """,
        database_id,
        start_time,
        "Running",
    )
    identity_result = cursor.fetchone()
    if not identity_result or identity_result[0] is None:
        cursor.connection.commit()
        raise RuntimeError('Failed to retrieve ExtractionID after starting extraction log')
    extraction_id = int(identity_result[0])
    cursor.connection.commit()
    return extraction_id


def update_extraction_log(
    cursor: pyodbc.Cursor,
    extraction_id: int,
    status: str,
    tables_loaded: int | None = None,
    columns_loaded: int | None = None,
    error_message: str | None = None,
) -> None:
    columns = ["EndTime = ?", "Status = ?"]
    params = [datetime.now(), status]

    if tables_loaded is not None:
        columns.append("TablesLoaded = ?")
        params.append(tables_loaded)
    if columns_loaded is not None:
        columns.append("ColumnsLoaded = ?")
        params.append(columns_loaded)
    if error_message is not None:
        columns.append("ErrorMessage = ?")
        params.append(error_message)

    params.append(extraction_id)

    cursor.execute(
        f"""
        UPDATE MetadataExtractionLog
        SET {', '.join(columns)}
        WHERE ExtractionID = ?
        """,
        *params,
    )
    cursor.connection.commit()


def extract_tables(source_conn: pyodbc.Connection) -> pd.DataFrame:
    tables_query = """
    SELECT
        TABLE_SCHEMA,
        TABLE_NAME,
        TABLE_TYPE
    FROM INFORMATION_SCHEMA.TABLES
    ORDER BY TABLE_SCHEMA, TABLE_NAME;
    """
    tables = pd.read_sql(tables_query, source_conn)
    logging.info('Extracted %d tables from source database', len(tables))
    return tables


def extract_columns(source_conn: pyodbc.Connection) -> pd.DataFrame:
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
    ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION;
    """
    columns = pd.read_sql(columns_query, source_conn)
    logging.info('Extracted %d columns from source database', len(columns))
    return columns


def load_tables(cursor: pyodbc.Cursor, database_id: int, tables: pd.DataFrame) -> int:
    existing_tables = {}
    cursor.execute(
        """
        SELECT TableID, SchemaName, TableName
        FROM MetadataTable
        WHERE DatabaseID = ?
        """,
        database_id,
    )
    for table_id, schema_name, table_name in cursor.fetchall():
        existing_tables[(schema_name, table_name)] = table_id

    table_inserts = 0
    for _, row in tables.iterrows():
        key = (row["TABLE_SCHEMA"], row["TABLE_NAME"])
        if key in existing_tables:
            continue
        cursor.execute(
            """
            INSERT INTO MetadataTable
            (DatabaseID, SchemaName, TableName, TableType)
            VALUES (?, ?, ?, ?)
            """,
            database_id,
            row["TABLE_SCHEMA"],
            row["TABLE_NAME"],
            row["TABLE_TYPE"],
        )
        existing_tables[key] = cursor.lastrowid if hasattr(cursor, 'lastrowid') else None
        table_inserts += 1

    cursor.connection.commit()
    logging.info('Inserted %d new tables into metadata repository', table_inserts)
    return table_inserts


def build_table_lookup(cursor: pyodbc.Cursor, database_id: int) -> dict[tuple[str, str], int]:
    cursor.execute(
        """
        SELECT TableID, SchemaName, TableName
        FROM MetadataTable
        WHERE DatabaseID = ?
        """,
        database_id,
    )
    return { (schema_name, table_name): table_id for table_id, schema_name, table_name in cursor.fetchall() }


def load_columns(cursor: pyodbc.Cursor, database_id: int, columns: pd.DataFrame) -> int:
    table_lookup = build_table_lookup(cursor, database_id)
    existing_columns = {}

    cursor.execute(
        """
        SELECT c.ColumnID, t.SchemaName, t.TableName, c.ColumnName
        FROM MetadataColumn c
        INNER JOIN MetadataTable t ON c.TableID = t.TableID
        WHERE t.DatabaseID = ?
        """,
        database_id,
    )
    for column_id, schema_name, table_name, column_name in cursor.fetchall():
        existing_columns[(schema_name, table_name, column_name)] = column_id

    column_inserts = 0
    for _, row in columns.iterrows():
        key = (row["TABLE_SCHEMA"], row["TABLE_NAME"], row["COLUMN_NAME"])
        table_key = (row["TABLE_SCHEMA"], row["TABLE_NAME"])
        table_id = table_lookup.get(table_key)
        if not table_id or key in existing_columns:
            continue

        max_length = row["CHARACTER_MAXIMUM_LENGTH"]
        max_length = None if pd.isna(max_length) else int(max_length)

        cursor.execute(
            """
            INSERT INTO MetadataColumn
            (TableID, ColumnName, DataType, MaxLength, IsNullable)
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
    logging.info('Inserted %d new columns into metadata repository', column_inserts)
    return column_inserts


def write_metadata_csv(output_dir: Path, filename: str, dataframe: pd.DataFrame) -> None:
    destination = output_dir / filename
    dataframe.to_csv(destination, index=False)
    logging.info('Wrote metadata CSV: %s', destination)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Extract metadata from SQL Server and load it into a repository.')
    parser.add_argument('--server', default=DEFAULT_SERVER, help='SQL Server name')
    parser.add_argument('--source-database', default=DEFAULT_SOURCE_DATABASE, help='Source database name')
    parser.add_argument('--repository-database', default=DEFAULT_REPOSITORY_DATABASE, help='Metadata repository database name')
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR, help='Directory for output CSV files')
    parser.add_argument('--driver', default=DEFAULT_DRIVER, help='ODBC driver for SQL Server')
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)

    output_dir = Path(args.output_dir)
    ensure_output_dir(output_dir)

    source_conn = None
    repo_conn = None
    extraction_id = None

    try:
        source_conn = connect_to_database(args.driver, args.server, args.source_database)
        logging.info('Connected to source database %s on server %s', args.source_database, args.server)

        repo_conn = connect_to_database(args.driver, args.server, args.repository_database)
        logging.info('Connected to repository database %s', args.repository_database)

        repo_cursor = repo_conn.cursor()

        database_id = get_database_id(repo_cursor, args.source_database, args.server)
        if database_id is None:
            logging.info('Source database not found in repository; inserting record')
            database_id = insert_database(repo_cursor, args.source_database, args.server)

        logging.info('Using DatabaseID %s', database_id)

        extraction_id = start_extraction_log(repo_cursor, database_id)
        logging.info('Started extraction log %s', extraction_id)

        tables = extract_tables(source_conn)
        tables_loaded = load_tables(repo_cursor, database_id, tables)
        write_metadata_csv(output_dir, 'tables_metadata.csv', tables)

        columns = extract_columns(source_conn)
        columns_loaded = load_columns(repo_cursor, database_id, columns)
        write_metadata_csv(output_dir, 'columns_metadata.csv', columns)

        update_extraction_log(
            repo_cursor,
            extraction_id,
            'Success',
            tables_loaded=tables_loaded,
            columns_loaded=columns_loaded,
        )
        logging.info('Extraction completed successfully')
        return 0

    except Exception as exc:
        logging.exception('Extraction failed')
        if extraction_id and repo_conn:
            update_extraction_log(
                repo_conn.cursor(),
                extraction_id,
                'Failed',
                error_message=str(exc),
            )
        return 1

    finally:
        if source_conn:
            source_conn.close()
            logging.info('Closed source database connection')
        if repo_conn:
            repo_conn.close()
            logging.info('Closed repository database connection')


if __name__ == '__main__':
    raise SystemExit(main())
