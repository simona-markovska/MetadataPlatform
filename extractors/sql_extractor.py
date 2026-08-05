import pyodbc
import pandas as pd
from datetime import datetime


# SQL Server connection details
server = "AXM345"
source_database = "AdventureWorks2022"
repository_database = "MetadataRepository"


# Variables for logging
extraction_id = None
tables_loaded = 0
columns_loaded = 0


try:

    # ---------------------------------
    # Connect to source database
    # ---------------------------------

    source_conn = pyodbc.connect(
        f"""
        DRIVER={{ODBC Driver 18 for SQL Server}};
        SERVER={server};
        DATABASE={source_database};
        Trusted_Connection=yes;
        TrustServerCertificate=yes;
        """
    )

    print("✅ Source database connection successful!")


    # ---------------------------------
    # Connect to metadata repository
    # ---------------------------------

    repo_conn = pyodbc.connect(
        f"""
        DRIVER={{ODBC Driver 18 for SQL Server}};
        SERVER={server};
        DATABASE={repository_database};
        Trusted_Connection=yes;
        TrustServerCertificate=yes;
        """
    )

    print("✅ Repository connection successful!\n")


    cursor = repo_conn.cursor()



    # ---------------------------------
    # Get DatabaseID
    # ---------------------------------

    cursor.execute(
        """
        SELECT DatabaseID
        FROM MetadataDatabase
        WHERE DatabaseName = ?
        AND ServerName = ?
        """,
        source_database,
        server
    )

    result = cursor.fetchone()


    if result:

        database_id = result[0]

        print(
            f"✅ Database already exists. DatabaseID: {database_id}\n"
        )

    else:

        cursor.execute(
            """
            INSERT INTO MetadataDatabase
            (
                DatabaseName,
                ServerName
            )
            VALUES (?, ?)
            """,
            source_database,
            server
        )

        repo_conn.commit()


        cursor.execute(
            """
            SELECT DatabaseID
            FROM MetadataDatabase
            WHERE DatabaseName = ?
            AND ServerName = ?
            """,
            source_database,
            server
        )

        database_id = cursor.fetchone()[0]


        print(
            f"✅ Database inserted. DatabaseID: {database_id}\n"
        )



    # ---------------------------------
    # Start extraction log
    # ---------------------------------

    start_time = datetime.now()


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
        "Running"
    )


    repo_conn.commit()


    cursor.execute(
        """
        SELECT @@IDENTITY
        """
    )

    extraction_id = int(cursor.fetchone()[0])


    print(
        f"✅ Extraction started. ID: {extraction_id}\n"
    )



    # ---------------------------------
    # Extract tables
    # ---------------------------------

    tables_query = """
    SELECT
        TABLE_SCHEMA,
        TABLE_NAME,
        TABLE_TYPE
    FROM INFORMATION_SCHEMA.TABLES
    ORDER BY TABLE_SCHEMA, TABLE_NAME;
    """


    tables = pd.read_sql(
        tables_query,
        source_conn
    )


    print("=== TABLE METADATA EXTRACTED ===")



    # ---------------------------------
    # Load tables
    # ---------------------------------

    for _, row in tables.iterrows():


        cursor.execute(
            """
            SELECT TableID
            FROM MetadataTable
            WHERE DatabaseID = ?
            AND SchemaName = ?
            AND TableName = ?
            """,
            database_id,
            row["TABLE_SCHEMA"],
            row["TABLE_NAME"]
        )


        existing_table = cursor.fetchone()



        if not existing_table:

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
                row["TABLE_TYPE"]
            )


            tables_loaded += 1



    repo_conn.commit()


    print(
        f"✅ Tables loaded: {tables_loaded}\n"
    )



    tables.to_csv(
        "output/tables_metadata.csv",
        index=False
    )



    # ---------------------------------
    # Extract columns
    # ---------------------------------

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


    columns = pd.read_sql(
        columns_query,
        source_conn
    )


    print("=== COLUMN METADATA EXTRACTED ===")



    # ---------------------------------
    # Load columns
    # ---------------------------------

    for _, row in columns.iterrows():


        cursor.execute(
            """
            SELECT TableID
            FROM MetadataTable
            WHERE DatabaseID = ?
            AND SchemaName = ?
            AND TableName = ?
            """,
            database_id,
            row["TABLE_SCHEMA"],
            row["TABLE_NAME"]
        )


        table_result = cursor.fetchone()



        if table_result:


            table_id = table_result[0]


            cursor.execute(
                """
                SELECT ColumnID
                FROM MetadataColumn
                WHERE TableID = ?
                AND ColumnName = ?
                """,
                table_id,
                row["COLUMN_NAME"]
            )


            existing_column = cursor.fetchone()



            if not existing_column:


                max_length = row["CHARACTER_MAXIMUM_LENGTH"]


                if pd.isna(max_length):
                    max_length = None

                else:
                    max_length = int(max_length)



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
                    str(row["IS_NULLABLE"])
                )


                columns_loaded += 1



    repo_conn.commit()


    print(
        f"✅ Columns loaded: {columns_loaded}\n"
    )



    columns.to_csv(
        "output/columns_metadata.csv",
        index=False
    )



    # ---------------------------------
    # Update extraction log - Success
    # ---------------------------------

    cursor.execute(
        """
        UPDATE MetadataExtractionLog
        SET
            EndTime = ?,
            Status = ?,
            TablesLoaded = ?,
            ColumnsLoaded = ?
        WHERE ExtractionID = ?
        """,
        datetime.now(),
        "Success",
        tables_loaded,
        columns_loaded,
        extraction_id
    )


    repo_conn.commit()


    print("✅ Extraction log updated successfully")



    # ---------------------------------
    # Close connections
    # ---------------------------------

    source_conn.close()
    repo_conn.close()


    print("✅ Connections closed")



except Exception as e:


    print("❌ Error occurred:")
    print(e)


    if extraction_id and 'repo_conn' in locals():

        cursor = repo_conn.cursor()


        cursor.execute(
            """
            UPDATE MetadataExtractionLog
            SET
                EndTime = ?,
                Status = ?,
                ErrorMessage = ?
            WHERE ExtractionID = ?
            """,
            datetime.now(),
            "Failed",
            str(e),
            extraction_id
        )


        repo_conn.commit()