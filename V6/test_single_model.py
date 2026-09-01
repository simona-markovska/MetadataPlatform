"""
Test a SINGLE Fabric semantic model all the way through
extraction -> MetadataRepository insertion.

TARGET:
    Workspace: AXM Demo Reports
    Workspace ID: ca34c4e0-b59c-4c4a-9ad1-398fcb660472
    Semantic Model: Test Report 1
    Fabric Model ID: fa39e415-ff4c-4bf4-a800-5239fde8a08c

IMPORTANT:
    - Only this semantic model is processed.
    - Uses the existing V6 semantic_model_extractor_v2.py classes.
    - Uses the real MetadataRepositoryWriter.
    - Actually writes to MetadataRepository_V6_Test.
    - No other semantic models are processed.

CHANGES IN THIS VERSION (FIXED):
    1. get_fabric_connection_string() now includes "LongAsMax=yes;"
       so ODBC Driver 18 binds long varchar/text parameters directly
       as varchar(max) instead of falling back to the legacy
       SQL_LONGVARCHAR -> text/ntext path (which is incompatible with
       the Latin1_General_100_BIN2_UTF8 collation used by Fabric
       Warehouse). This is what caused:

           Cannot convert to text/ntext or collate to
           'Latin1_General_100_BIN2_UTF8' ...

    2. The manual repair_mojibake()/repair_metadata_unicode()
       workaround has been REMOVED. It was only needed because the
       *previous* production connection was missing
       connection.setencoding(encoding="utf-8") /
       connection.setdecoding(..., encoding="utf-8"), which caused
       non-ASCII characters (e.g. Δ, ▲, −) to be mis-encoded on the
       way into SQL Server, producing mojibake like "â–²". This test
       script already had the setencoding/setdecoding calls, so with
       the connection string fix, data should now round-trip
       correctly WITHOUT needing any text repair step. Keeping a
       repair step around a fixed root cause risks corrupting text
       that is already correct.
"""

import logging
import sys
import traceback
from pathlib import Path

import pyodbc


# ============================================================================
# PROJECT PATH
# ============================================================================

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# ============================================================================
# IMPORT EXISTING V6 EXTRACTOR
# ============================================================================

from config.config import DEFAULT_DRIVER
from src.fabric.client import FabricClient

from V6.semantic_model_extractor_v2 import (
    SemanticModelExtractor,
    RepositoryValidator,
    MetadataRepositoryWriter,
)


# ============================================================================
# TEST CONFIGURATION
# ============================================================================

WORKSPACE_NAME = "AXM Demo Reports"

WORKSPACE_ID = (
    "ca34c4e0-b59c-4c4a-9ad1-398fcb660472"
)

SEMANTIC_MODEL_NAME = "Test Report 1"

FABRIC_MODEL_ID = (
    "fa39e415-ff4c-4bf4-a800-5239fde8a08c"
)


# ============================================================================
# METADATA REPOSITORY
# ============================================================================

FABRIC_SQL_SERVER = (
    "j7mjaqg22d2ujb27llpciiyism-7jnw46tiqcde5cpv233ctk345u"
    ".datawarehouse.fabric.microsoft.com"
)

FABRIC_SQL_DATABASE = "MetadataRepository_V6_Test"


# ============================================================================
# LOGGING
# ============================================================================

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
)


# ============================================================================
# MOJIBAKE DETECTION (VALIDATION ONLY — NOT REPAIR)
# ============================================================================
#
# We keep a read-only detector so the test can PROVE the fix worked
# (i.e. assert that zero mojibake markers appear after extraction and
# after reading data back from the database). We deliberately do NOT
# repair/mutate strings anymore — the connection-string + encoding fix
# should make that unnecessary. If this check still fires after the
# fix, that tells us the root cause is not fully resolved yet, which
# is exactly what we want to know from this test.
# ============================================================================

MOJIBAKE_MARKERS = (
    "Ã",
    "Â",
    "Ð",
    "Ñ",
    "Î",
    "Ï",
    "â",
    "â€™",
    "â€œ",
    "â€",
    "â€“",
    "â€”",
    "âˆ",
    "â–",
    "Â©",
    "Â®",
    "Â°",
)


def looks_like_mojibake(text: str) -> bool:

    if not isinstance(text, str):
        return False

    return any(
        marker in text
        for marker in MOJIBAKE_MARKERS
    )


def count_mojibake(value) -> int:

    """
    Count strings that still look like mojibake.
    """

    if isinstance(value, str):

        return 1 if looks_like_mojibake(value) else 0

    if isinstance(value, dict):

        return sum(
            count_mojibake(v)
            for v in value.values()
        )

    if isinstance(value, list):

        return sum(
            count_mojibake(v)
            for v in value
        )

    if isinstance(value, tuple):

        return sum(
            count_mojibake(v)
            for v in value
        )

    return 0


def print_measure_unicode_check(measures):

    print()
    print("-" * 80)
    print("UNICODE / MOJIBAKE CHECK (post-extraction, in-memory)")
    print("-" * 80)

    good_unicode_count = 0
    bad_count = 0

    for measure in measures:

        name = measure.get(
            "measure_name",
            "",
        )

        dax = measure.get(
            "expression",
            "",
        )

        if looks_like_mojibake(str(name)):

            bad_count += 1

            print(
                f"WARNING: Measure name still contains "
                f"mojibake: {name}"
            )

        if looks_like_mojibake(str(dax)):

            bad_count += 1

            print(
                f"WARNING: DAX for measure "
                f"'{name}' still contains mojibake."
            )

        elif any(
            char in str(dax)
            for char in ("Δ", "▲", "▼", "−", "’")
        ):
            good_unicode_count += 1

    print(
        f"Measures with correct special Unicode characters: "
        f"{good_unicode_count}"
    )

    print(
        f"Measures with mojibake detected: "
        f"{bad_count}"
    )


# ============================================================================
# FABRIC WAREHOUSE CONNECTION
# ============================================================================

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
        # FIX: forces ODBC Driver 18 to bind long text parameters as
        # varchar(max)/nvarchar(max) instead of falling back to the
        # legacy SQL_LONGVARCHAR -> text/ntext path, which is
        # incompatible with UTF-8 collations (Latin1_General_100_BIN2_UTF8).
        "LongAsMax=yes;"
    )


def _decode_utf8_column(raw_bytes):
    """
    Output converter for long UTF-8-collation varchar(max) columns.

    IMPORTANT: this receives the FULLY REASSEMBLED raw bytes for the
    column value (pyodbc has already looped over all internal
    SQLGetData chunks and concatenated them before calling this
    function). Decoding here, once, on the complete byte string
    avoids the bug where connection.setdecoding(SQL_CHAR, "utf-8")
    can decode individual internal chunks separately and fail with
    "unexpected end of data" whenever a multi-byte UTF-8 character
    (e.g. Δ = 0xCE 0x94) happens to fall across a chunk boundary.
    """

    if raw_bytes is None:
        return None

    return raw_bytes.decode("utf-8")


def connect_to_fabric_warehouse():

    connection_string = get_fabric_connection_string(
        DEFAULT_DRIVER,
        FABRIC_SQL_SERVER,
        FABRIC_SQL_DATABASE,
    )

    logging.info(
        "Opening Microsoft Entra interactive authentication..."
    )

    connection = pyodbc.connect(
        connection_string
    )

    # READS: use an output converter instead of setdecoding(). This
    # avoids the pyodbc chunked-SQLGetData decode bug that produces
    # "UnicodeDecodeError ... unexpected end of data" on long
    # varchar(max) columns with non-ASCII content.
    connection.add_output_converter(
        pyodbc.SQL_CHAR,
        _decode_utf8_column,
    )

    connection.add_output_converter(
        pyodbc.SQL_VARCHAR,
        _decode_utf8_column,
    )

    # WRITES: deliberately do NOT call connection.setencoding(...).
    # Forcing SQL_C_CHAR/UTF-8 binding on outgoing parameters caused
    # the ODBC driver to reinterpret those bytes through the client's
    # ANSI codepage (e.g. cp1252) before converting them into the
    # UTF-8-collation column -- double-encoding characters like
    # Δ (UTF-8 bytes 0xCE 0x94) into "Î”". Leaving encoding unset lets
    # pyodbc use its default SQL_C_WCHAR / UTF-16LE binding for
    # outgoing strings, which the driver converts unambiguously into
    # the UTF-8-collation varchar(max) columns. Combined with
    # LongAsMax=yes in the connection string, this writes long,
    # non-ASCII DAX expressions correctly (confirmed by direct SSMS
    # inspection of the stored data).

    return connection


# ============================================================================
# PRINT EXTRACTED METADATA
# ============================================================================

def print_extraction_summary(
    tables,
    columns,
    measures,
    relationships,
    source_mappings,
    calculated_tables,
):

    print()
    print("-" * 80)
    print("EXTRACTION SUMMARY")
    print("-" * 80)

    print(f"Tables:             {len(tables)}")
    print(f"Columns:            {len(columns)}")
    print(f"Measures:           {len(measures)}")
    print(f"Relationships:      {len(relationships)}")
    print(f"Source mappings:    {len(source_mappings)}")
    print(f"Calculated tables:  {len(calculated_tables)}")

    print()

    print("Tables:")

    for table in tables:

        print(
            f"  - {table['table_name']} "
            f"[{table['table_type']}]"
        )

    print()

    print("Source mappings:")

    for mapping in source_mappings:

        print(
            f"  - {mapping.get('semantic_table')}"
            f" | Type={mapping.get('source_type')}"
            f" | Server={mapping.get('server')}"
            f" | Database={mapping.get('database')}"
            f" | Schema={mapping.get('schema')}"
            f" | Table={mapping.get('source_table')}"
        )


# ============================================================================
# PRINT SELECTED MEASURES
# ============================================================================

def print_unicode_measure_samples(measures):

    print()
    print("-" * 80)
    print("UNICODE MEASURE SAMPLES (post-extraction, in-memory)")
    print("-" * 80)

    interesting_names = (
        "CAR",
        "CET1 Ratio",
        "Tier 1 Ratio",
        "CAR Δ La",
        "CET1 Δ La",
        "Tier 1 Δ La",
        "LCR Δ La",
        "NSFR Δ La",
        "Regulatory Position Narrative",
        "Net Profit (dyn)",
        "Net Profit LY (dyn)",
    )

    found = 0

    for measure in measures:

        name = measure.get(
            "measure_name",
            "",
        )

        if name in interesting_names:

            dax = measure.get(
                "expression",
                "",
            )

            print()
            print(f"Measure: {name}")
            print(f"DAX: {dax[:500]}")

            found += 1

    if found == 0:

        print(
            "No selected Unicode measures found."
        )


# ============================================================================
# MAIN TEST
# ============================================================================

def main():

    print("=" * 80)
    print("SINGLE SEMANTIC MODEL REPOSITORY TEST (FIXED)")
    print("=" * 80)

    print()
    print(f"WORKSPACE:       {WORKSPACE_NAME}")
    print(f"WORKSPACE ID:    {WORKSPACE_ID}")
    print(f"SEMANTIC MODEL:  {SEMANTIC_MODEL_NAME}")
    print(f"FABRIC MODEL ID: {FABRIC_MODEL_ID}")

    print()
    print(
        "IMPORTANT: ONLY THIS MODEL WILL BE PROCESSED."
    )

    print()
    print(
        "This run uses LongAsMax=yes + add_output_converter(utf-8) for "
        "reads, and pyodbc's default WCHAR binding (no setencoding) for "
        "writes."
    )
    print(
        "No manual mojibake repair is applied — we are validating "
        "that the root-cause fix makes it unnecessary."
    )

    connection = None

    try:

        # ====================================================================
        # 1. FABRIC CLIENT
        # ====================================================================

        print()
        print("-" * 80)
        print("1. CREATING FABRIC CLIENT")
        print("-" * 80)

        client = FabricClient()

        print(
            "Fabric client created successfully."
        )


        # ====================================================================
        # 2. VERIFY TARGET MODEL
        # ====================================================================

        print()
        print("-" * 80)
        print("2. VERIFYING TARGET SEMANTIC MODEL")
        print("-" * 80)

        print(
            "No workspace-wide processing will be performed."
        )

        model = {
            "id": FABRIC_MODEL_ID,
            "displayName": SEMANTIC_MODEL_NAME,
        }

        print(
            f"Target model: {model['displayName']}"
        )

        print(
            f"Fabric Model ID: {model['id']}"
        )


        # ====================================================================
        # 3. RETRIEVE DEFINITION
        # ====================================================================

        print()
        print("-" * 80)
        print("3. RETRIEVING SEMANTIC MODEL DEFINITION")
        print("-" * 80)

        definition = client.get_semantic_model_definition(
            WORKSPACE_ID,
            FABRIC_MODEL_ID,
        )

        print(
            "Semantic model definition retrieved successfully."
        )


        # ====================================================================
        # 4. CREATE EXTRACTOR
        # ====================================================================

        print()
        print("-" * 80)
        print("4. CREATING SEMANTIC MODEL EXTRACTOR")
        print("-" * 80)

        extractor = SemanticModelExtractor(
            definition,
            workspace_id=WORKSPACE_ID,
            semantic_model_id=FABRIC_MODEL_ID,
            semantic_model_name=SEMANTIC_MODEL_NAME,
        )

        print(
            "Extractor created successfully."
        )


        # ====================================================================
        # 5. EXTRACT METADATA
        # ====================================================================

        print()
        print("-" * 80)
        print("5. EXTRACTING METADATA")
        print("-" * 80)

        tables = extractor.extract_tables()

        columns = extractor.extract_columns(
            tables
        )

        measures = extractor.extract_measures(
            tables
        )

        relationships = extractor.extract_relationships()

        source_mappings = extractor.extract_source_mappings(
            tables
        )

        calculated_tables = (
            extractor.extract_calculated_tables(
                tables
            )
        )

        print_extraction_summary(
            tables,
            columns,
            measures,
            relationships,
            source_mappings,
            calculated_tables,
        )


        # ====================================================================
        # 5A. VALIDATE UNICODE IN MEMORY (no repair — detection only)
        # ====================================================================

        print()
        print("-" * 80)
        print("5A. VALIDATING UNICODE IN EXTRACTED METADATA (IN-MEMORY)")
        print("-" * 80)

        in_memory_mojibake_count = (
            count_mojibake(tables)
            + count_mojibake(columns)
            + count_mojibake(measures)
            + count_mojibake(relationships)
            + count_mojibake(source_mappings)
            + count_mojibake(calculated_tables)
        )

        print(
            f"Mojibake-looking strings found in extracted metadata: "
            f"{in_memory_mojibake_count}"
        )

        if in_memory_mojibake_count == 0:

            print(
                "PASSED: no mojibake detected straight out of "
                "_decode_part() (base64 -> utf-8)."
            )

        else:

            print(
                "WARNING: mojibake already present in memory, "
                "before any database round-trip. This would point "
                "to an issue in _decode_part() or the Fabric API "
                "response itself, not the SQL connection."
            )

        print_measure_unicode_check(
            measures
        )

        print_unicode_measure_samples(
            measures
        )


        # ====================================================================
        # 6. CONNECT TO METADATA REPOSITORY
        # ====================================================================

        print()
        print("-" * 80)
        print("6. CONNECTING TO METADATA REPOSITORY")
        print("-" * 80)

        print(
            f"Repository database: "
            f"{FABRIC_SQL_DATABASE}"
        )

        connection = connect_to_fabric_warehouse()

        print(
            "Connected to MetadataRepository successfully."
        )

        cursor = connection.cursor()

        repository_validator = RepositoryValidator(
            cursor
        )

        repository_writer = MetadataRepositoryWriter(
            cursor
        )


        # ====================================================================
        # 7. REPOSITORY INSERTION
        # ====================================================================

        print()
        print("=" * 80)
        print("7. STARTING REAL METADATA REPOSITORY INSERTION")
        print("=" * 80)

        print()
        print(
            "This section uses the actual V6 repository writer."
        )


        # ====================================================================
        # 7A. WORKSPACE
        # ====================================================================

        print()
        print(
            "[7A] Synchronizing workspace..."
        )

        repository_writer.upsert_workspace(
            workspace_id=WORKSPACE_ID,
            workspace_name=WORKSPACE_NAME,
            is_enabled=True,
        )

        print(
            "Workspace synchronized successfully."
        )


        # ====================================================================
        # 7B. SEMANTIC MODEL
        # ====================================================================

        print()
        print(
            "[7B] Creating/updating semantic model..."
        )

        semantic_model_id = (
            repository_writer.get_or_create_model(
                {
                    "semantic_model_name":
                        SEMANTIC_MODEL_NAME,

                    "workspace_id":
                        WORKSPACE_ID,

                    "workspace_name":
                        WORKSPACE_NAME,

                    "fabric_model_id":
                        FABRIC_MODEL_ID,
                }
            )
        )

        print(
            f"Repository SemanticModelID: "
            f"{semantic_model_id}"
        )


        # ====================================================================
        # 7C. CLEAR OLD METADATA
        # ====================================================================

        print()
        print(
            "[7C] Clearing previous metadata for this model..."
        )

        repository_writer.clear_model_metadata(
            semantic_model_id
        )

        print(
            "Previous model metadata cleared."
        )


        # ====================================================================
        # 7D. TABLES
        # ====================================================================

        print()
        print(
            "[7D] INSERTING SEMANTIC TABLES..."
        )

        table_ids = repository_writer.insert_tables(
            semantic_model_id,
            tables,
        )

        print(
            f"Semantic tables inserted: "
            f"{len(table_ids)}"
        )


        # ====================================================================
        # 7E. COLUMNS
        # ====================================================================

        print()
        print(
            "[7E] INSERTING SEMANTIC COLUMNS..."
        )

        column_ids = repository_writer.insert_columns(
            table_ids,
            columns,
        )

        print(
            f"Semantic columns inserted: "
            f"{len(column_ids)}"
        )


        # ====================================================================
        # 7F. TABLE SOURCE MAPPINGS
        # ====================================================================

        print()
        print(
            "[7F] INSERTING TABLE SOURCE MAPPINGS..."
        )

        repository_writer.insert_table_sources(
            table_ids,
            tables,
            source_mappings,
            repository_validator,
        )

        print(
            "Table source mapping stage completed."
        )


        # ====================================================================
        # 7G. COLUMN SOURCE MAPPINGS
        # ====================================================================

        print()
        print(
            "[7G] INSERTING COLUMN SOURCE MAPPINGS..."
        )

        repository_writer.insert_column_sources(
            column_ids,
            columns,
            source_mappings,
            repository_validator,
        )

        print(
            "Column source mapping stage completed."
        )


        # ====================================================================
        # 7H. CALCULATED COLUMN DEPENDENCIES
        # ====================================================================

        print()
        print(
            "[7H] INSERTING CALCULATED COLUMN DEPENDENCIES..."
        )

        repository_writer.insert_column_dependencies(
            columns,
            column_ids,
        )

        print(
            "Calculated column dependency stage completed."
        )


        # ====================================================================
        # 7I. MEASURES  <-- this is the stage that previously failed
        # ====================================================================

        print()
        print(
            "[7I] INSERTING MEASURES..."
        )

        measure_ids = repository_writer.insert_measures(
            semantic_model_id,
            table_ids,
            measures,
        )

        print(
            f"Measures inserted: "
            f"{len(measure_ids)}"
        )


        # ====================================================================
        # 7J. MEASURE DEPENDENCIES
        # ====================================================================

        print()
        print(
            "[7J] INSERTING MEASURE DEPENDENCIES..."
        )

        repository_writer.insert_measure_dependencies(
            measures,
            measure_ids,
            table_ids,
            column_ids,
        )

        print(
            "Measure dependency stage completed."
        )


        # ====================================================================
        # 7K. CALCULATED TABLE DEPENDENCIES
        # ====================================================================

        print()
        print(
            "[7K] INSERTING CALCULATED TABLE DEPENDENCIES..."
        )

        repository_writer.insert_table_dependencies(
            calculated_tables,
            table_ids,
            column_ids,
        )

        print(
            "Calculated table dependency stage completed."
        )


        # ====================================================================
        # 7L. RELATIONSHIPS
        # ====================================================================

        print()
        print(
            "[7L] INSERTING SEMANTIC RELATIONSHIPS..."
        )

        repository_writer.insert_relationships(
            semantic_model_id,
            table_ids,
            column_ids,
            relationships,
        )

        print(
            "Semantic relationship stage completed."
        )


        # ====================================================================
        # 8. COMMIT
        # ====================================================================

        print()
        print("-" * 80)
        print("8. COMMITTING REPOSITORY TRANSACTION")
        print("-" * 80)

        connection.commit()

        print(
            "Repository transaction committed successfully."
        )


        # ====================================================================
        # 9. VALIDATE INSERTED DATA
        # ====================================================================

        print()
        print("-" * 80)
        print("9. VALIDATING REPOSITORY DATA")
        print("-" * 80)

        cursor.execute(
            """
            SELECT
                ModelName,
                WorkspaceName,
                FabricModelID,
                SourceType
            FROM dbo.MetadataSemanticModel
            WHERE SemanticModelID = ?
            """,
            semantic_model_id,
        )

        model_row = cursor.fetchone()

        if model_row:

            print()
            print(
                "Semantic model repository record:"
            )

            print(
                f"  ModelName:      {model_row[0]}"
            )

            print(
                f"  WorkspaceName:  {model_row[1]}"
            )

            print(
                f"  FabricModelID:  {model_row[2]}"
            )

            print(
                f"  SourceType:     {model_row[3]}"
            )

        else:

            print(
                "WARNING: Semantic model record "
                "could not be found after commit."
            )


        # ====================================================================
        # 9A. COUNTS
        # ====================================================================

        validation_queries = {

            "Semantic Tables": """
                SELECT COUNT(*)
                FROM dbo.MetadataSemanticTable
                WHERE SemanticModelID = ?
            """,

            "Semantic Columns": """
                SELECT COUNT(*)
                FROM dbo.MetadataSemanticColumn C
                INNER JOIN dbo.MetadataSemanticTable T
                    ON C.SemanticTableID = T.SemanticTableID
                WHERE T.SemanticModelID = ?
            """,

            "Measures": """
                SELECT COUNT(*)
                FROM dbo.MetadataMeasure
                WHERE SemanticModelID = ?
            """,

            "Relationships": """
                SELECT COUNT(*)
                FROM dbo.MetadataSemanticRelationship
                WHERE SemanticModelID = ?
            """,

            "Table Sources": """
                SELECT COUNT(*)
                FROM dbo.MetadataSemanticTableSource S
                INNER JOIN dbo.MetadataSemanticTable T
                    ON S.SemanticTableID = T.SemanticTableID
                WHERE T.SemanticModelID = ?
            """,

            "Column Sources": """
                SELECT COUNT(*)
                FROM dbo.MetadataSemanticColumnSource S
                INNER JOIN dbo.MetadataSemanticColumn C
                    ON S.SemanticColumnID = C.SemanticColumnID
                INNER JOIN dbo.MetadataSemanticTable T
                    ON C.SemanticTableID = T.SemanticTableID
                WHERE T.SemanticModelID = ?
            """,

        }

        print()
        print(
            "Repository counts:"
        )

        for label, query in validation_queries.items():

            cursor.execute(
                query,
                semantic_model_id,
            )

            row = cursor.fetchone()

            count = (
                int(row[0])
                if row
                else 0
            )

            print(
                f"  {label:<20}: {count}"
            )


        # ====================================================================
        # 9B. VALIDATE ACTUAL UNICODE IN DATABASE (the real test)
        # ====================================================================

        print()
        print("-" * 80)
        print("9B. VALIDATING UNICODE STORED IN DATABASE (READ-BACK)")
        print("-" * 80)

        cursor.execute(
            """
            SELECT
                MeasureName,
                DAXExpression
            FROM dbo.MetadataMeasure
            WHERE SemanticModelID = ?
            ORDER BY MeasureID
            """,
            semantic_model_id,
        )

        database_measures = cursor.fetchall()

        print(
            f"Measures read back from repository: "
            f"{len(database_measures)}"
        )

        database_mojibake_count = 0
        database_good_unicode_count = 0

        for row in database_measures:

            measure_name = row[0] or ""
            dax_expression = row[1] or ""

            if looks_like_mojibake(
                str(measure_name)
            ):

                database_mojibake_count += 1

                print(
                    f"WARNING: Database measure name "
                    f"contains mojibake: "
                    f"{measure_name}"
                )

            if looks_like_mojibake(
                str(dax_expression)
            ):

                database_mojibake_count += 1

                print(
                    f"WARNING: Database DAX for "
                    f"'{measure_name}' contains mojibake."
                )

            elif any(
                char in str(dax_expression)
                for char in ("Δ", "▲", "▼", "−", "’")
            ):
                database_good_unicode_count += 1

        print()
        print(
            f"Measures with correct special Unicode after DB round-trip: "
            f"{database_good_unicode_count}"
        )

        if database_mojibake_count == 0:

            print()
            print(
                "DATABASE UNICODE CHECK PASSED."
            )

            print(
                "No mojibake found in the inserted measure metadata. "
                "LongAsMax=yes + utf-8 encoding fix appears to have "
                "resolved BOTH the insert error and the character "
                "corruption."
            )

        else:

            print()
            print(
                f"WARNING: Detected "
                f"{database_mojibake_count} "
                f"possible mojibake values in database. "
                "The connection-string fix may not be sufficient — "
                "investigate further before rolling out to the main "
                "script."
            )


        # ====================================================================
        # 9C. PRINT IMPORTANT MEASURES FROM DATABASE
        # ====================================================================

        print()
        print("-" * 80)
        print("9C. SAMPLE MEASURES READ FROM DATABASE")
        print("-" * 80)

        interesting_database_measures = (
            "CAR Δ La",
            "CET1 Δ La",
            "Tier 1 Δ La",
            "LCR Δ La",
            "NSFR Δ La",
            "Regulatory Position Narrative",
        )

        printed = 0

        for row in database_measures:

            measure_name = row[0] or ""

            if measure_name in (
                interesting_database_measures
            ):

                print()
                print(
                    f"Measure: {measure_name}"
                )

                print(
                    f"DAX: {row[1]}"
                )

                printed += 1

        if printed == 0:

            print(
                "No selected Unicode measures "
                "were found by exact name. "
                "(Only relevant if this specific model contains them.)"
            )


        # ====================================================================
        # 10. FINAL SUCCESS
        # ====================================================================

        print()
        print("=" * 80)
        print("TEST COMPLETED SUCCESSFULLY")
        print("=" * 80)

        print()
        print(
            f"Workspace:          {WORKSPACE_NAME}"
        )

        print(
            f"Workspace ID:       {WORKSPACE_ID}"
        )

        print(
            f"Semantic Model:     {SEMANTIC_MODEL_NAME}"
        )

        print(
            f"Fabric Model ID:    {FABRIC_MODEL_ID}"
        )

        print(
            f"Repository Model ID:{semantic_model_id}"
        )

        print()
        print(
            "The model was extracted AND inserted "
            "into MetadataRepository."
        )

        print()
        print(
            "No other semantic models were processed."
        )


    except Exception as exc:

        print()
        print("=" * 80)
        print("TEST FAILED")
        print("=" * 80)

        print()
        print(
            f"Stage failed with error: {exc}"
        )

        print()
        print("FULL TRACEBACK")
        print("-" * 80)

        traceback.print_exc()

        if connection:

            print()
            print(
                "Rolling back current transaction..."
            )

            try:

                connection.rollback()

                print(
                    "Rollback completed."
                )

            except Exception as rollback_exc:

                print(
                    f"Rollback failed: "
                    f"{rollback_exc}"
                )

        print()
        print(
            "IMPORTANT: Check the stage immediately "
            "above the error."
        )

        raise


    finally:

        if connection:

            try:

                connection.close()

                logging.info(
                    "MetadataRepository connection closed."
                )

            except Exception:

                pass


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    main()