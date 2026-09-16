import csv
import os
from datetime import datetime
from pathlib import Path

import pyodbc


# =============================================================================
# CONFIGURATION
# =============================================================================

# Your Fabric Warehouse SQL endpoint.
# Example:
# mywarehouse.datawarehouse.fabric.microsoft.com
FABRIC_SERVER = os.getenv(
    "FABRIC_SERVER",
    "j7mjaqg22d2ujb27llpciiyism-7jnw46tiqcde5cpv233ctk345u.datawarehouse.fabric.microsoft.com"
)

DATABASE = "MetadataRepository"

ODBC_DRIVER = "ODBC Driver 18 for SQL Server"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = PROJECT_ROOT / "backups"


# =============================================================================
# CONNECTION
# =============================================================================

def get_connection():
    connection_string = (
        f"DRIVER={{{ODBC_DRIVER}}};"
        f"SERVER={FABRIC_SERVER};"
        f"DATABASE={DATABASE};"
        "Authentication=ActiveDirectoryInteractive;"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
    )

    print("=" * 80)
    print("CONNECTING TO METADATA REPOSITORY")
    print("=" * 80)
    print()
    print("A Microsoft Entra login window may appear...")
    print()

    connection = pyodbc.connect(connection_string)

    print("Connected successfully.")
    print()

    return connection


# =============================================================================
# HELPERS
# =============================================================================

def quote_identifier(name):
    """
    Safely quote a SQL Server/Fabric SQL identifier.
    """
    return "[" + name.replace("]", "]]") + "]"


def sql_string(value):
    """
    Convert a Python value to a SQL string literal.
    """
    if value is None:
        return "NULL"

    value = str(value).replace("'", "''")
    return f"N'{value}'"


def sql_type(row):
    """
    Build a SQL data type definition from INFORMATION_SCHEMA.COLUMNS.
    """

    data_type = row["DATA_TYPE"].lower()

    max_length = row["CHARACTER_MAXIMUM_LENGTH"]
    numeric_precision = row["NUMERIC_PRECISION"]
    numeric_scale = row["NUMERIC_SCALE"]
    datetime_precision = row["DATETIME_PRECISION"]

    # Character types
    if data_type in ("varchar", "char", "varbinary", "binary"):
        if max_length == -1:
            return f"{data_type}(MAX)"
        return f"{data_type}({max_length})"

    if data_type in ("nvarchar", "nchar"):
        if max_length == -1:
            return f"{data_type}(MAX)"

        # INFORMATION_SCHEMA reports nvarchar length in characters
        return f"{data_type}({max_length})"

    # Decimal / numeric
    if data_type in ("decimal", "numeric"):
        if numeric_precision is not None and numeric_scale is not None:
            return f"{data_type}({numeric_precision},{numeric_scale})"

    # Date/time types
    if data_type in ("datetime2", "datetimeoffset", "time"):
        if datetime_precision is not None:
            return f"{data_type}({datetime_precision})"

    return data_type


# =============================================================================
# DISCOVER TABLES
# =============================================================================

def get_tables(cursor):

    query = """
        SELECT
            TABLE_SCHEMA,
            TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
          AND TABLE_SCHEMA = 'dbo'
        ORDER BY TABLE_NAME;
    """

    cursor.execute(query)

    tables = []

    for row in cursor.fetchall():
        tables.append(
            {
                "schema": row.TABLE_SCHEMA,
                "name": row.TABLE_NAME,
            }
        )

    return tables


# =============================================================================
# GET COLUMNS
# =============================================================================

def get_columns(cursor, schema_name, table_name):

    query = """
        SELECT
            COLUMN_NAME,
            DATA_TYPE,
            CHARACTER_MAXIMUM_LENGTH,
            NUMERIC_PRECISION,
            NUMERIC_SCALE,
            DATETIME_PRECISION,
            IS_NULLABLE,
            ORDINAL_POSITION,
            COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ?
          AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION;
    """

    cursor.execute(query, schema_name, table_name)

    columns = []

    for row in cursor.fetchall():

        columns.append(
            {
                "COLUMN_NAME": row.COLUMN_NAME,
                "DATA_TYPE": row.DATA_TYPE,
                "CHARACTER_MAXIMUM_LENGTH": row.CHARACTER_MAXIMUM_LENGTH,
                "NUMERIC_PRECISION": row.NUMERIC_PRECISION,
                "NUMERIC_SCALE": row.NUMERIC_SCALE,
                "DATETIME_PRECISION": row.DATETIME_PRECISION,
                "IS_NULLABLE": row.IS_NULLABLE,
                "ORDINAL_POSITION": row.ORDINAL_POSITION,
                "COLUMN_DEFAULT": row.COLUMN_DEFAULT,
            }
        )

    return columns


# =============================================================================
# GENERATE SCHEMA.SQL
# =============================================================================

def generate_schema(cursor, tables, output_file):

    print("=" * 80)
    print("GENERATING SCHEMA")
    print("=" * 80)
    print()

    with open(output_file, "w", encoding="utf-8") as f:

        f.write("-- ================================================================\n")
        f.write("-- MetadataRepository schema backup\n")
        f.write(f"-- Generated: {datetime.now().isoformat()}\n")
        f.write("-- ================================================================\n\n")

        for table in tables:

            schema_name = table["schema"]
            table_name = table["name"]

            print(f"  Schema: {schema_name}.{table_name}")

            columns = get_columns(
                cursor,
                schema_name,
                table_name
            )

            f.write(
                f"IF OBJECT_ID(N'{schema_name}.{table_name}', N'U') "
                f"IS NULL\n"
            )

            f.write("BEGIN\n")

            f.write(
                f"    CREATE TABLE "
                f"{quote_identifier(schema_name)}."
                f"{quote_identifier(table_name)}\n"
            )

            f.write("    (\n")

            definitions = []

            for column in columns:

                definition = (
                    f"        "
                    f"{quote_identifier(column['COLUMN_NAME'])} "
                    f"{sql_type(column)}"
                )

                if column["IS_NULLABLE"] == "NO":
                    definition += " NOT NULL"
                else:
                    definition += " NULL"

                if column["COLUMN_DEFAULT"] is not None:
                    definition += (
                        f" DEFAULT {column['COLUMN_DEFAULT']}"
                    )

                definitions.append(definition)

            f.write(",\n".join(definitions))
            f.write("\n")

            f.write("    );\n")
            f.write("END;\n")
            f.write("GO\n\n")

    print()
    print(f"Schema written to: {output_file}")
    print()


# =============================================================================
# EXPORT TABLE TO CSV
# =============================================================================

def export_table_to_csv(
    cursor,
    schema_name,
    table_name,
    output_file
):

    query = (
        f"SELECT * FROM "
        f"{quote_identifier(schema_name)}."
        f"{quote_identifier(table_name)}"
    )

    cursor.execute(query)

    column_names = [
        column[0]
        for column in cursor.description
    ]

    row_count = 0

    with open(
        output_file,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as csv_file:

        writer = csv.writer(csv_file)

        writer.writerow(column_names)

        while True:

            rows = cursor.fetchmany(1000)

            if not rows:
                break

            writer.writerows(rows)

            row_count += len(rows)

    return row_count


# =============================================================================
# EXPORT ALL DATA
# =============================================================================

def export_all_data(cursor, tables, data_directory):

    print("=" * 80)
    print("EXPORTING TABLE DATA")
    print("=" * 80)
    print()

    results = []

    for index, table in enumerate(tables, start=1):

        schema_name = table["schema"]
        table_name = table["name"]

        output_file = (
            data_directory /
            f"{table_name}.csv"
        )

        print(
            f"[{index}/{len(tables)}] "
            f"{schema_name}.{table_name}"
        )

        try:

            row_count = export_table_to_csv(
                cursor,
                schema_name,
                table_name,
                output_file
            )

            file_size_mb = (
                output_file.stat().st_size /
                1024 /
                1024
            )

            print(
                f"    Rows: {row_count:,}"
            )

            print(
                f"    Size: {file_size_mb:.2f} MB"
            )

            results.append(
                {
                    "schema": schema_name,
                    "table": table_name,
                    "rows": row_count,
                    "status": "SUCCESS",
                    "file": output_file.name,
                }
            )

        except Exception as exc:

            print(
                f"    ERROR: {exc}"
            )

            results.append(
                {
                    "schema": schema_name,
                    "table": table_name,
                    "rows": 0,
                    "status": f"FAILED: {exc}",
                    "file": output_file.name,
                }
            )

    print()

    return results


# =============================================================================
# BACKUP INFO
# =============================================================================

def write_backup_info(
    output_file,
    tables,
    results,
    start_time,
    end_time
):

    duration = end_time - start_time

    successful = sum(
        1
        for result in results
        if result["status"] == "SUCCESS"
    )

    failed = len(results) - successful

    total_rows = sum(
        result["rows"]
        for result in results
    )

    with open(output_file, "w", encoding="utf-8") as f:

        f.write("MetadataRepository Backup\n")
        f.write("=" * 60 + "\n\n")

        f.write(
            f"Backup started:  {start_time.isoformat()}\n"
        )

        f.write(
            f"Backup finished: {end_time.isoformat()}\n"
        )

        f.write(
            f"Duration:        {duration}\n\n"
        )

        f.write(f"Server:          {FABRIC_SERVER}\n")
        f.write(f"Database:        {DATABASE}\n\n")

        f.write(
            f"Tables discovered: {len(tables)}\n"
        )

        f.write(
            f"Tables successful: {successful}\n"
        )

        f.write(
            f"Tables failed:     {failed}\n"
        )

        f.write(
            f"Total rows:        {total_rows:,}\n\n"
        )

        f.write("Table results\n")
        f.write("-" * 60 + "\n")

        for result in results:

            f.write(
                f"{result['schema']}."
                f"{result['table']} | "
                f"Rows: {result['rows']:,} | "
                f"{result['status']} | "
                f"{result['file']}\n"
            )


# =============================================================================
# MAIN
# =============================================================================

def main():

    start_time = datetime.now()

    timestamp = start_time.strftime(
        "%Y-%m-%d_%H%M%S"
    )

    backup_directory = (
        BACKUP_ROOT /
        f"MetadataRepository_{timestamp}"
    )

    data_directory = (
        backup_directory /
        "data"
    )

    backup_directory.mkdir(
        parents=True,
        exist_ok=True
    )

    data_directory.mkdir(
        parents=True,
        exist_ok=True
    )

    print()
    print("=" * 80)
    print("METADATA REPOSITORY BACKUP")
    print("=" * 80)
    print()
    print(f"Database: {DATABASE}")
    print(f"Backup:   {backup_directory}")
    print()

    connection = None

    try:

        connection = get_connection()

        cursor = connection.cursor()

        # -------------------------------------------------------------
        # Discover tables
        # -------------------------------------------------------------

        print("=" * 80)
        print("DISCOVERING TABLES")
        print("=" * 80)
        print()

        tables = get_tables(cursor)

        print(
            f"Found {len(tables)} dbo tables."
        )
        print()

        if not tables:
            print(
                "No dbo tables were found."
            )
            return

        # -------------------------------------------------------------
        # Schema
        # -------------------------------------------------------------

        schema_file = (
            backup_directory /
            "schema.sql"
        )

        generate_schema(
            cursor,
            tables,
            schema_file
        )

        # -------------------------------------------------------------
        # Data
        # -------------------------------------------------------------

        results = export_all_data(
            cursor,
            tables,
            data_directory
        )

        # -------------------------------------------------------------
        # Backup info
        # -------------------------------------------------------------

        end_time = datetime.now()

        info_file = (
            backup_directory /
            "backup_info.txt"
        )

        write_backup_info(
            info_file,
            tables,
            results,
            start_time,
            end_time
        )

        # -------------------------------------------------------------
        # Summary
        # -------------------------------------------------------------

        successful = sum(
            1
            for result in results
            if result["status"] == "SUCCESS"
        )

        failed = len(results) - successful

        total_rows = sum(
            result["rows"]
            for result in results
        )

        print("=" * 80)
        print("BACKUP COMPLETE")
        print("=" * 80)
        print()

        print(f"Backup folder: {backup_directory}")
        print(f"Tables:        {len(tables)}")
        print(f"Successful:    {successful}")
        print(f"Failed:        {failed}")
        print(f"Total rows:    {total_rows:,}")
        print()

        if failed:
            print(
                "WARNING: Some tables failed to export."
            )
            print(
                f"See: {info_file}"
            )
        else:
            print(
                "All tables exported successfully."
            )

        print()

    except Exception as exc:

        print()
        print("=" * 80)
        print("BACKUP FAILED")
        print("=" * 80)
        print()
        print(exc)
        print()

        raise

    finally:

        if connection is not None:
            connection.close()


if __name__ == "__main__":
    main()