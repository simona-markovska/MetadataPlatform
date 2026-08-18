import base64
import logging
import re
import sys
from pathlib import Path

import pyodbc


# ===========================================================================
# PROJECT PATH
# ===========================================================================

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from config.config import DEFAULT_DRIVER
from src.fabric.client import FabricClient


# ===========================================================================
# CONFIGURATION
# ===========================================================================

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


# ---------------------------------------------------------------------------
# Fabric workspace
# ---------------------------------------------------------------------------

WORKSPACE_ID = "7a6e5bfa-8068-4e86-89f5-d6f629ab7ced"

WORKSPACE_NAME = "Metadata Intelligence Platform"


# ---------------------------------------------------------------------------
# Source SQL database
# ---------------------------------------------------------------------------

SOURCE_DATABASE = "AdventureWorks2022"
SOURCE_SERVER = "AXM345"


# ---------------------------------------------------------------------------
# Fabric Warehouse
# ---------------------------------------------------------------------------

FABRIC_SQL_SERVER = (
    "j7mjaqg22d2ujb27llpciiyism-7jnw46tiqcde5cpv233ctk345u"
    ".datawarehouse.fabric.microsoft.com"
)

FABRIC_SQL_DATABASE = "MetadataRepository"


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
        "Opening Microsoft Entra interactive authentication..."
    )

    return pyodbc.connect(connection_string)


# ===========================================================================
# SEMANTIC MODEL EXTRACTOR
# ===========================================================================


class SemanticModelExtractor:
    """
    Extract metadata from a Microsoft Fabric semantic model definition.

    Extracts:

        - Tables
        - Table classifications
        - Columns
        - Measures
        - DAX expressions
        - Relationships
    """

    def __init__(
        self,
        definition,
        workspace_id=None,
        semantic_model_id=None,
        semantic_model_name=None,
    ):

        self.definition = definition

        self.workspace_id = workspace_id
        self.semantic_model_id = semantic_model_id
        self.semantic_model_name = semantic_model_name

        self.parts = (
            definition
            .get("definition", {})
            .get("parts", [])
        )

    # =======================================================================
    # PAYLOAD
    # =======================================================================

    def _decode_part(self, part):

        payload = part.get("payload")

        if not payload:
            return ""

        payload_type = part.get("payloadType")

        if payload_type != "InlineBase64":
            return ""

        try:

            decoded = base64.b64decode(payload)

            return decoded.decode(
                "utf-8",
                errors="replace",
            )

        except Exception:

            logging.exception(
                "Failed to decode definition part"
            )

            return ""

    # =======================================================================
    # TABLES
    # =======================================================================

    def extract_tables(self):

        tables = []

        for part in self.parts:

            path = part.get(
                "path",
                "",
            )

            if not path.startswith(
                "definition/tables/"
            ):
                continue

            if not path.endswith(
                ".tmdl"
            ):
                continue

            content = self._decode_part(part)

            table_name = self._extract_table_name(
                content
            )

            if not table_name:
                continue

            table_type = self._classify_table(
                table_name,
                content,
                path,
            )

            tables.append(
                {
                    "table_name": table_name,
                    "table_type": table_type,
                    "definition_path": path,
                }
            )

        return tables

    # =======================================================================
    # TABLE CLASSIFICATION
    # =======================================================================

    @staticmethod
    def _classify_table(
        table_name,
        content,
        path,
    ):

        lower_name = table_name.lower()
        lower_content = content.lower()

        # -------------------------------------------------------------------
        # Date template
        # -------------------------------------------------------------------

        if table_name.startswith(
            "DateTableTemplate_"
        ):

            return "Date Template"

        # -------------------------------------------------------------------
        # Auto date
        # -------------------------------------------------------------------

        if table_name.startswith(
            "LocalDateTable_"
        ):

            return "Auto Date"

        # -------------------------------------------------------------------
        # Explicit measure table
        # -------------------------------------------------------------------

        if lower_name in {
            "dax_measures",
            "measures",
            "measure table",
        }:

            return "Measure Table"

        # -------------------------------------------------------------------
        # Calculated table
        # -------------------------------------------------------------------

        calculated_partition = re.search(
            r"(?im)^\s*partition\s+.+?\s*=\s*calculated\s*$",
            content,
        )

        if calculated_partition:

            return "Calculated"

        # -------------------------------------------------------------------
        # Additional calculated-table detection
        # -------------------------------------------------------------------

        calculated_expression = re.search(
            r"(?ims)"
            r"^\s*partition\s+.+?\s*=\s*calculated\s*"
            r".*?"
            r"(?:^|\n)\s*expression\s*=",
            content,
        )

        if calculated_expression:

            return "Calculated"

        # -------------------------------------------------------------------
        # Additional calculated indicators
        # -------------------------------------------------------------------

        calculated_indicators = [
            "tablekind = calculated",
            "calculatedtable",
            "calculationgroup",
        ]

        for indicator in calculated_indicators:

            if indicator in lower_content:

                return "Calculated"

        # -------------------------------------------------------------------
        # Measure table based on TMDL metadata
        # -------------------------------------------------------------------

        has_measure = bool(
            re.search(
                r"(?m)^\s*measure\s+",
                content,
            )
        )

        has_display_folder = (
            "displayfolder:" in lower_content
        )

        has_measure_group = (
            "measuregroup" in lower_content
        )

        if (
            has_measure
            and (
                has_display_folder
                or has_measure_group
            )
        ):

            return "Measure Table"

        # -------------------------------------------------------------------
        # Source table
        # -------------------------------------------------------------------

        return "Source"

    # =======================================================================
    # COLUMNS
    # =======================================================================

    def extract_columns(self):

        columns = []

        for part in self.parts:

            path = part.get(
                "path",
                "",
            )

            if not path.startswith(
                "definition/tables/"
            ):
                continue

            if not path.endswith(
                ".tmdl"
            ):
                continue

            content = self._decode_part(part)

            table_name = self._extract_table_name(
                content
            )

            if not table_name:
                continue

            for line in content.splitlines():

                stripped = line.strip()

                if not stripped.startswith(
                    "column "
                ):
                    continue

                definition = (
                    stripped[len("column "):]
                    .strip()
                )

                column_name = (
                    self._extract_object_name(
                        definition
                    )
                )

                if not column_name:
                    continue

                columns.append(
                    {
                        "table_name": table_name,
                        "column_name": column_name,
                        "definition_path": path,
                    }
                )

        return columns

    # =======================================================================
    # MEASURES
    # =======================================================================

    def extract_measures(self):

        measures = []

        property_pattern = re.compile(
            r"^\s*("
            r"formatString"
            r"|displayFolder"
            r"|lineageTag"
            r"|description"
            r"|isHidden"
            r"|dataCategory"
            r"|formatStringDefinition"
            r"|extendedProperties"
            r"|annotation"
            r"|changedProperty"
            r"|modifiedTime"
            r"|ref"
            r"|expression"
            r"|partition"
            r"|column"
            r"|measure"
            r"|table"
            r")\s*[:=]",
            re.IGNORECASE,
        )

        for part in self.parts:

            path = part.get(
                "path",
                "",
            )

            if not path.endswith(
                ".tmdl"
            ):
                continue

            content = self._decode_part(part)

            lines = content.splitlines()

            index = 0

            while index < len(lines):

                stripped = lines[index].strip()

                if not stripped.startswith(
                    "measure "
                ):

                    index += 1
                    continue

                measure_definition = (
                    stripped[len("measure "):]
                    .strip()
                )

                # -----------------------------------------------------------
                # Quoted measure
                # -----------------------------------------------------------

                match = re.match(
                    r"'([^']+)'\s*=\s*(.*)",
                    measure_definition,
                )

                if match:

                    measure_name = match.group(1)
                    expression = match.group(2)

                else:

                    # -------------------------------------------------------
                    # Unquoted measure
                    # -------------------------------------------------------

                    match = re.match(
                        r"([^\s=]+)\s*=\s*(.*)",
                        measure_definition,
                    )

                    if not match:

                        index += 1
                        continue

                    measure_name = match.group(1)
                    expression = match.group(2)

                # -----------------------------------------------------------
                # Collect DAX expression
                # -----------------------------------------------------------

                expression_lines = []

                if expression:

                    expression_lines.append(
                        expression.strip()
                    )

                next_index = index + 1

                while next_index < len(lines):

                    next_line = lines[next_index]

                    stripped_next = next_line.strip()

                    # -------------------------------------------------------
                    # Empty line inside DAX
                    # -------------------------------------------------------

                    if stripped_next == "":

                        if expression_lines:

                            expression_lines.append("")

                        next_index += 1
                        continue

                    # -------------------------------------------------------
                    # Stop when another measure starts
                    # -------------------------------------------------------

                    if stripped_next.startswith(
                        "measure "
                    ):

                        break

                    # -------------------------------------------------------
                    # Stop when another object starts
                    # -------------------------------------------------------

                    if stripped_next.startswith(
                        "column "
                    ):

                        break

                    if stripped_next.startswith(
                        "partition "
                    ):

                        break

                    if stripped_next.startswith(
                        "table "
                    ):

                        break

                    # -------------------------------------------------------
                    # TMDL properties are not DAX
                    # -------------------------------------------------------

                    if property_pattern.match(
                        stripped_next
                    ):

                        break

                    # -------------------------------------------------------
                    # DAX continuation must be indented
                    # -------------------------------------------------------

                    if (
                        not next_line.startswith(" ")
                        and not next_line.startswith("\t")
                    ):

                        break

                    expression_lines.append(
                        stripped_next
                    )

                    next_index += 1

                # -----------------------------------------------------------
                # Clean expression
                # -----------------------------------------------------------

                while (
                    expression_lines
                    and expression_lines[-1] == ""
                ):

                    expression_lines.pop()

                full_expression = "\n".join(
                    expression_lines
                ).strip()

                measures.append(
                    {
                        "measure_name": measure_name,
                        "expression": full_expression,
                        "definition_path": path,
                    }
                )

                index = next_index

        return measures

    # =======================================================================
    # RELATIONSHIPS
    # =======================================================================

    def extract_relationships(self):

        relationships = []

        relationship_part = None

        for part in self.parts:

            if part.get("path") == (
                "definition/relationships.tmdl"
            ):

                relationship_part = part
                break

        if not relationship_part:

            return relationships

        content = self._decode_part(
            relationship_part
        )

        lines = content.splitlines()

        current_relationship = None

        for line in lines:

            stripped = line.strip()

            # ----------------------------------------------------------------
            # Relationship
            # ----------------------------------------------------------------

            if stripped.startswith(
                "relationship "
            ):

                if current_relationship:

                    relationships.append(
                        current_relationship
                    )

                relationship_id = (
                    stripped[
                        len("relationship "):
                    ].strip()
                )

                current_relationship = {
                    "relationship_id":
                        relationship_id,

                    "from_table": None,
                    "from_column": None,

                    "to_table": None,
                    "to_column": None,
                }

            # ----------------------------------------------------------------
            # From
            # ----------------------------------------------------------------

            elif stripped.startswith(
                "fromColumn:"
            ):

                value = stripped[
                    len("fromColumn:"):
                ].strip()

                table, column = (
                    self._split_table_column(
                        value
                    )
                )

                if current_relationship:

                    current_relationship[
                        "from_table"
                    ] = table

                    current_relationship[
                        "from_column"
                    ] = column

            # ----------------------------------------------------------------
            # To
            # ----------------------------------------------------------------

            elif stripped.startswith(
                "toColumn:"
            ):

                value = stripped[
                    len("toColumn:"):
                ].strip()

                table, column = (
                    self._split_table_column(
                        value
                    )
                )

                if current_relationship:

                    current_relationship[
                        "to_table"
                    ] = table

                    current_relationship[
                        "to_column"
                    ] = column

        if current_relationship:

            relationships.append(
                current_relationship
            )

        return relationships

    # =======================================================================
    # HELPERS
    # =======================================================================

    @staticmethod
    def _extract_table_name(content):

        for line in content.splitlines():

            stripped = line.strip()

            if stripped.startswith(
                "table "
            ):

                return (
                    stripped[
                        len("table "):
                    ]
                    .strip()
                    .strip("'")
                )

        return None

    @staticmethod
    def _extract_object_name(value):

        if value.startswith("'"):

            match = re.match(
                r"'([^']+)'",
                value,
            )

            if match:

                return match.group(1)

        parts = value.split()

        return (
            parts[0]
            if parts
            else None
        )

    @staticmethod
    def _split_table_column(value):

        match = re.match(
            r"'([^']+)'\.([^\s]+)",
            value,
        )

        if match:

            return (
                match.group(1),
                match.group(2),
            )

        if "." in value:

            table, column = value.rsplit(
                ".",
                1,
            )

            return (
                table.strip("'"),
                column,
            )

        return (
            None,
            value,
        )


# ===========================================================================
# SEMANTIC MODEL
# ===========================================================================


def get_semantic_model_id(
    cursor,
    model_name,
    fabric_model_id,
):

    cursor.execute(
        """
        SELECT SemanticModelID
        FROM dbo.MetadataSemanticModel
        WHERE FabricModelID = ?
        """,
        fabric_model_id,
    )

    result = cursor.fetchone()

    if result:

        return int(result[0])

    cursor.execute(
        """
        INSERT INTO dbo.MetadataSemanticModel
        (
            ModelName,
            WorkspaceID,
            WorkspaceName,
            FabricModelID,
            SourceType
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        model_name,
        WORKSPACE_ID,
        None,
        fabric_model_id,
        "Microsoft Fabric Semantic Model",
    )

    cursor.connection.commit()

    cursor.execute(
        """
        SELECT MAX(SemanticModelID)
        FROM dbo.MetadataSemanticModel
        WHERE FabricModelID = ?
        """,
        fabric_model_id,
    )

    result = cursor.fetchone()

    if not result or result[0] is None:

        raise RuntimeError(
            "Could not retrieve SemanticModelID."
        )

    return int(result[0])


# ===========================================================================
# SEMANTIC TABLES
# ===========================================================================


def load_semantic_tables(
    cursor,
    semantic_model_id,
    tables,
):

    semantic_table_lookup = {}

    cursor.execute(
        """
        SELECT
            SemanticTableID,
            TableName
        FROM dbo.MetadataSemanticTable
        WHERE SemanticModelID = ?
        """,
        semantic_model_id,
    )

    for (
        semantic_table_id,
        table_name,
    ) in cursor.fetchall():

        semantic_table_lookup[
            table_name
        ] = int(semantic_table_id)

    inserted = 0

    for table in tables:

        table_name = table["table_name"]
        table_type = table["table_type"]
        definition_path = table["definition_path"]

        semantic_table_id = (
            semantic_table_lookup.get(
                table_name
            )
        )

        if semantic_table_id is None:

            cursor.execute(
                """
                INSERT INTO dbo.MetadataSemanticTable
                (
                    SemanticModelID,
                    TableName,
                    TableType,
                    DefinitionPath
                )
                VALUES (?, ?, ?, ?)
                """,
                semantic_model_id,
                table_name,
                table_type,
                definition_path,
            )

            cursor.connection.commit()

            cursor.execute(
                """
                SELECT MAX(SemanticTableID)
                FROM dbo.MetadataSemanticTable
                WHERE SemanticModelID = ?
                  AND TableName = ?
                """,
                semantic_model_id,
                table_name,
            )

            result = cursor.fetchone()

            if not result or result[0] is None:

                raise RuntimeError(
                    f"Could not retrieve SemanticTableID "
                    f"for {table_name}"
                )

            semantic_table_id = int(
                result[0]
            )

            semantic_table_lookup[
                table_name
            ] = semantic_table_id

            inserted += 1

        else:

            cursor.execute(
                """
                UPDATE dbo.MetadataSemanticTable
                SET
                    TableType = ?,
                    DefinitionPath = ?
                WHERE SemanticTableID = ?
                """,
                table_type,
                definition_path,
                semantic_table_id,
            )

    cursor.connection.commit()

    logging.info(
        "Semantic tables inserted: %d",
        inserted,
    )

    return semantic_table_lookup


# ===========================================================================
# SOURCE TABLE LOOKUP
# ===========================================================================


def build_source_table_lookup(
    cursor,
    database_id,
):

    cursor.execute(
        """
        SELECT
            TableID,
            SchemaName,
            TableName
        FROM dbo.MetadataTable
        WHERE DatabaseID = ?
        """,
        database_id,
    )

    return {
        (
            schema_name,
            table_name,
        ): int(table_id)

        for (
            table_id,
            schema_name,
            table_name,
        ) in cursor.fetchall()
    }


# ===========================================================================
# SEMANTIC TABLE -> SOURCE TABLE
# ===========================================================================


def load_semantic_table_sources(
    cursor,
    semantic_table_lookup,
    source_table_lookup,
):

    inserted = 0

    cursor.execute(
        """
        SELECT
            SemanticTableID,
            TableName,
            TableType
        FROM dbo.MetadataSemanticTable
        WHERE SemanticTableID IN ({})
        """.format(
            ",".join(
                "?" for _ in semantic_table_lookup.values()
            )
        ),
        *semantic_table_lookup.values(),
    )

    semantic_tables = cursor.fetchall()

    for (
        semantic_table_id,
        semantic_table_name,
        table_type,
    ) in semantic_tables:

        if table_type != "Source":

            continue

        parts = semantic_table_name.split(
            " ",
            1,
        )

        if len(parts) != 2:

            continue

        schema_name = parts[0]
        table_name = parts[1]

        source_table_id = source_table_lookup.get(
            (
                schema_name,
                table_name,
            )
        )

        if source_table_id is None:

            logging.debug(
                "No source SQL table found for semantic table %s",
                semantic_table_name,
            )

            continue

        cursor.execute(
            """
            SELECT 1
            FROM dbo.MetadataSemanticTableSource
            WHERE SemanticTableID = ?
              AND TableID = ?
            """,
            semantic_table_id,
            source_table_id,
        )

        if cursor.fetchone():

            continue

        cursor.execute(
            """
            INSERT INTO dbo.MetadataSemanticTableSource
            (
                SemanticTableID,
                TableID
            )
            VALUES (?, ?)
            """,
            semantic_table_id,
            source_table_id,
        )

        inserted += 1

    cursor.connection.commit()

    logging.info(
        "Semantic-to-source table links inserted: %d",
        inserted,
    )

    return inserted


# ===========================================================================
# SEMANTIC COLUMNS
# ===========================================================================


def load_semantic_columns(
    cursor,
    semantic_table_lookup,
    columns,
):

    semantic_column_lookup = {}

    semantic_table_ids = list(
        semantic_table_lookup.values()
    )

    if not semantic_table_ids:

        return semantic_column_lookup

    cursor.execute(
        """
        SELECT
            SemanticColumnID,
            SemanticTableID,
            ColumnName
        FROM dbo.MetadataSemanticColumn
        WHERE SemanticTableID IN ({})
        """.format(
            ",".join(
                "?" for _ in semantic_table_ids
            )
        ),
        *semantic_table_ids,
    )

    for (
        semantic_column_id,
        semantic_table_id,
        column_name,
    ) in cursor.fetchall():

        semantic_column_lookup[
            (
                int(semantic_table_id),
                column_name,
            )
        ] = int(semantic_column_id)

    inserted = 0

    for column in columns:

        table_name = column["table_name"]
        column_name = column["column_name"]
        definition_path = column["definition_path"]

        semantic_table_id = (
            semantic_table_lookup.get(
                table_name
            )
        )

        if semantic_table_id is None:

            continue

        key = (
            semantic_table_id,
            column_name,
        )

        semantic_column_id = (
            semantic_column_lookup.get(
                key
            )
        )

        if semantic_column_id is None:

            cursor.execute(
                """
                INSERT INTO dbo.MetadataSemanticColumn
                (
                    SemanticTableID,
                    ColumnName,
                    DefinitionPath
                )
                VALUES (?, ?, ?)
                """,
                semantic_table_id,
                column_name,
                definition_path,
            )

            cursor.connection.commit()

            cursor.execute(
                """
                SELECT MAX(SemanticColumnID)
                FROM dbo.MetadataSemanticColumn
                WHERE SemanticTableID = ?
                  AND ColumnName = ?
                """,
                semantic_table_id,
                column_name,
            )

            result = cursor.fetchone()

            if not result or result[0] is None:

                raise RuntimeError(
                    f"Could not retrieve SemanticColumnID "
                    f"for {table_name}.{column_name}"
                )

            semantic_column_id = int(
                result[0]
            )

            semantic_column_lookup[
                key
            ] = semantic_column_id

            inserted += 1

        else:

            cursor.execute(
                """
                UPDATE dbo.MetadataSemanticColumn
                SET DefinitionPath = ?
                WHERE SemanticColumnID = ?
                """,
                definition_path,
                semantic_column_id,
            )

    cursor.connection.commit()

    logging.info(
        "Semantic columns inserted: %d",
        inserted,
    )

    return semantic_column_lookup


# ===========================================================================
# SOURCE COLUMN LOOKUP
# ===========================================================================


def build_source_column_lookup(
    cursor,
    database_id,
):

    cursor.execute(
        """
        SELECT
            c.ColumnID,
            t.SchemaName,
            t.TableName,
            c.ColumnName
        FROM dbo.MetadataColumn c
        INNER JOIN dbo.MetadataTable t
            ON c.TableID = t.TableID
        WHERE t.DatabaseID = ?
        """,
        database_id,
    )

    return {
        (
            schema_name,
            table_name,
            column_name,
        ): int(column_id)

        for (
            column_id,
            schema_name,
            table_name,
            column_name,
        ) in cursor.fetchall()
    }


# ===========================================================================
# SEMANTIC COLUMN -> SOURCE COLUMN
# ===========================================================================


def load_semantic_column_sources(
    cursor,
    semantic_column_lookup,
    semantic_table_lookup,
    source_column_lookup,
):

    inserted = 0

    reverse_table_lookup = {
        table_id: table_name
        for table_name, table_id
        in semantic_table_lookup.items()
    }

    for (
        semantic_key,
        semantic_column_id,
    ) in semantic_column_lookup.items():

        semantic_table_id, column_name = (
            semantic_key
        )

        semantic_table_name = (
            reverse_table_lookup.get(
                semantic_table_id
            )
        )

        if not semantic_table_name:

            continue

        parts = semantic_table_name.split(
            " ",
            1,
        )

        if len(parts) != 2:

            continue

        schema_name = parts[0]
        table_name = parts[1]

        source_column_id = (
            source_column_lookup.get(
                (
                    schema_name,
                    table_name,
                    column_name,
                )
            )
        )

        if source_column_id is None:

            continue

        cursor.execute(
            """
            SELECT 1
            FROM dbo.MetadataSemanticColumnSource
            WHERE SemanticColumnID = ?
              AND ColumnID = ?
            """,
            semantic_column_id,
            source_column_id,
        )

        if cursor.fetchone():

            continue

        cursor.execute(
            """
            INSERT INTO dbo.MetadataSemanticColumnSource
            (
                SemanticColumnID,
                ColumnID
            )
            VALUES (?, ?)
            """,
            semantic_column_id,
            source_column_id,
        )

        inserted += 1

    cursor.connection.commit()

    logging.info(
        "Semantic-to-source column links inserted: %d",
        inserted,
    )

    return inserted


# ===========================================================================
# MEASURES
# ===========================================================================


def load_measures(
    cursor,
    semantic_model_id,
    tables,
    semantic_table_lookup,
    measures,
):

    inserted = 0

    # -----------------------------------------------------------------------
    # Determine actual measure table
    # -----------------------------------------------------------------------

    measure_table_id = None

    for table in tables:

        if table["table_type"] != "Measure Table":

            continue

        table_name = table["table_name"]

        measure_table_id = (
            semantic_table_lookup.get(
                table_name
            )
        )

        if measure_table_id is not None:

            logging.info(
                "Detected measure table: %s",
                table_name,
            )

            break

    # -----------------------------------------------------------------------
    # Load measures
    # -----------------------------------------------------------------------

    for measure in measures:

        measure_name = measure[
            "measure_name"
        ]

        expression = measure[
            "expression"
        ]

        definition_path = measure[
            "definition_path"
        ]

        cursor.execute(
            """
            SELECT
                MeasureID
            FROM dbo.MetadataMeasure
            WHERE SemanticModelID = ?
              AND MeasureName = ?
            """,
            semantic_model_id,
            measure_name,
        )

        result = cursor.fetchone()

        if result:

            measure_id = int(
                result[0]
            )

            cursor.execute(
                """
                UPDATE dbo.MetadataMeasure
                SET
                    SemanticTableID = ?,
                    DAXExpression = ?,
                    DefinitionPath = ?
                WHERE MeasureID = ?
                """,
                measure_table_id,
                expression,
                definition_path,
                measure_id,
            )

        else:

            cursor.execute(
                """
                INSERT INTO dbo.MetadataMeasure
                (
                    SemanticModelID,
                    SemanticTableID,
                    MeasureName,
                    DAXExpression,
                    DefinitionPath
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                semantic_model_id,
                measure_table_id,
                measure_name,
                expression,
                definition_path,
            )

            inserted += 1

    cursor.connection.commit()

    logging.info(
        "Measures inserted: %d",
        inserted,
    )

    return inserted


# ===========================================================================
# MEASURE DEPENDENCIES
# ===========================================================================


def load_measure_dependencies(
    cursor,
    semantic_model_id,
    semantic_table_lookup,
    semantic_column_lookup,
    measures,
):

    inserted = 0
    deleted = 0

    column_reference_pattern = re.compile(
        r"'([^']+)'\s*\[\s*([^\]]+?)\s*\]"
    )

    for measure in measures:

        measure_name = measure[
            "measure_name"
        ]

        dax_expression = measure[
            "expression"
        ]

        cursor.execute(
            """
            SELECT
                MeasureID
            FROM dbo.MetadataMeasure
            WHERE SemanticModelID = ?
              AND MeasureName = ?
            """,
            semantic_model_id,
            measure_name,
        )

        result = cursor.fetchone()

        if not result:

            logging.warning(
                "Could not find MeasureID for measure: %s",
                measure_name,
            )

            continue

        measure_id = int(
            result[0]
        )

        cursor.execute(
            """
            DELETE FROM dbo.MetadataSemanticMeasureDependency
            WHERE MeasureID = ?
            """,
            measure_id,
        )

        deleted += max(
            cursor.rowcount,
            0,
        )

        references = (
            column_reference_pattern.findall(
                dax_expression
            )
        )

        if not references:

            logging.info(
                "No column dependencies found for measure: %s",
                measure_name,
            )

            continue

        unique_references = list(
            dict.fromkeys(
                references
            )
        )

        for (
            table_name,
            column_name,
        ) in unique_references:

            semantic_table_id = (
                semantic_table_lookup.get(
                    table_name
                )
            )

            if semantic_table_id is None:

                logging.warning(
                    "Could not resolve semantic table "
                    "'%s' for measure '%s'.",
                    table_name,
                    measure_name,
                )

                continue

            semantic_column_id = (
                semantic_column_lookup.get(
                    (
                        semantic_table_id,
                        column_name,
                    )
                )
            )

            if semantic_column_id is None:

                logging.warning(
                    "Could not resolve semantic column "
                    "'%s[%s]' for measure '%s'.",
                    table_name,
                    column_name,
                    measure_name,
                )

                continue

            cursor.execute(
                """
                INSERT INTO dbo.MetadataSemanticMeasureDependency
                (
                    MeasureID,
                    SemanticTableID,
                    SemanticColumnID,
                    DependencyType,
                    DependencyExpression
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                measure_id,
                semantic_table_id,
                semantic_column_id,
                "Column",
                f"'{table_name}'[{column_name}]",
            )

            inserted += 1

            logging.debug(
                "Measure dependency: %s -> %s[%s]",
                measure_name,
                table_name,
                column_name,
            )

    cursor.connection.commit()

    logging.info(
        "Measure dependencies inserted: %d",
        inserted,
    )

    logging.info(
        "Previous measure dependencies removed: %d",
        deleted,
    )

    return inserted


# ===========================================================================
# SEMANTIC RELATIONSHIPS
# ===========================================================================


def load_semantic_relationships(
    cursor,
    semantic_model_id,
    semantic_table_lookup,
    semantic_column_lookup,
    relationships,
):

    inserted = 0

    for relationship in relationships:

        from_table = relationship[
            "from_table"
        ]

        from_column = relationship[
            "from_column"
        ]

        to_table = relationship[
            "to_table"
        ]

        to_column = relationship[
            "to_column"
        ]

        from_table_id = (
            semantic_table_lookup.get(
                from_table
            )
        )

        to_table_id = (
            semantic_table_lookup.get(
                to_table
            )
        )

        if (
            from_table_id is None
            or to_table_id is None
        ):

            logging.warning(
                "Could not resolve semantic relationship tables: "
                "%s -> %s",
                from_table,
                to_table,
            )

            continue

        from_column_id = (
            semantic_column_lookup.get(
                (
                    from_table_id,
                    from_column,
                )
            )
        )

        to_column_id = (
            semantic_column_lookup.get(
                (
                    to_table_id,
                    to_column,
                )
            )
        )

        if (
            from_column_id is None
            or to_column_id is None
        ):

            logging.warning(
                "Could not resolve semantic relationship columns: "
                "%s.%s -> %s.%s",
                from_table,
                from_column,
                to_table,
                to_column,
            )

            continue

        cursor.execute(
            """
            SELECT 1
            FROM dbo.MetadataSemanticRelationship
            WHERE SemanticModelID = ?
              AND FromTableID = ?
              AND FromColumnID = ?
              AND ToTableID = ?
              AND ToColumnID = ?
            """,
            semantic_model_id,
            from_table_id,
            from_column_id,
            to_table_id,
            to_column_id,
        )

        if cursor.fetchone():

            continue

        cursor.execute(
            """
            INSERT INTO dbo.MetadataSemanticRelationship
            (
                SemanticModelID,
                FromTableID,
                FromColumnID,
                ToTableID,
                ToColumnID
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            semantic_model_id,
            from_table_id,
            from_column_id,
            to_table_id,
            to_column_id,
        )

        inserted += 1

    cursor.connection.commit()

    logging.info(
        "Semantic relationships inserted: %d",
        inserted,
    )

    return inserted


# ===========================================================================
# PRINT SUMMARY
# ===========================================================================


def print_summary(
    tables,
    columns,
    measures,
    relationships,
):

    print()
    print("=" * 70)
    print("TABLE SUMMARY")
    print("=" * 70)

    source_tables = [
        t for t in tables
        if t["table_type"] == "Source"
    ]

    calculated_tables = [
        t for t in tables
        if t["table_type"] == "Calculated"
    ]

    auto_date_tables = [
        t for t in tables
        if t["table_type"] == "Auto Date"
    ]

    date_templates = [
        t for t in tables
        if t["table_type"] == "Date Template"
    ]

    measure_tables = [
        t for t in tables
        if t["table_type"] == "Measure Table"
    ]

    print(
        f"Source tables:       {len(source_tables)}"
    )

    print(
        f"Calculated tables:   {len(calculated_tables)}"
    )

    print(
        f"Auto Date tables:    {len(auto_date_tables)}"
    )

    print(
        f"Date templates:      {len(date_templates)}"
    )

    print(
        f"Measure tables:      {len(measure_tables)}"
    )

    print(
        f"Total tables:        {len(tables)}"
    )

    print()

    print(
        f"Total columns:       {len(columns)}"
    )

    print(
        f"Total measures:      {len(measures)}"
    )

    print(
        f"Total relationships: {len(relationships)}"
    )


# ===========================================================================
# PROCESS ONE SEMANTIC MODEL
# ===========================================================================


def process_semantic_model(
    client,
    cursor,
    database_id,
    fabric_model_id,
    semantic_model_name,
):

    # =======================================================================
    # 1. RETRIEVE SEMANTIC MODEL DEFINITION
    # =======================================================================

    print()
    print("=" * 70)
    print(
        f"SEMANTIC MODEL: {semantic_model_name}"
    )
    print("=" * 70)

    print()
    print(
        "Retrieving semantic model definition..."
    )

    definition = (
        client.get_semantic_model_definition(
            WORKSPACE_ID,
            fabric_model_id,
        )
    )

    # =======================================================================
    # 2. EXTRACT
    # =======================================================================

    extractor = SemanticModelExtractor(
        definition,
        workspace_id=WORKSPACE_ID,
        semantic_model_id=fabric_model_id,
        semantic_model_name=semantic_model_name,
    )

    tables = extractor.extract_tables()

    columns = extractor.extract_columns()

    measures = extractor.extract_measures()

    relationships = (
        extractor.extract_relationships()
    )

    # =======================================================================
    # 3. PRINT CLASSIFICATION
    # =======================================================================

    print()
    print("=" * 70)
    print("TABLE CLASSIFICATION")
    print("=" * 70)

    for table in tables:

        print(
            f"- {table['table_name']}"
            f" | Type: {table['table_type']}"
        )

    print_summary(
        tables,
        columns,
        measures,
        relationships,
    )

    # =======================================================================
    # 4. SEMANTIC MODEL
    # =======================================================================

    print()
    print(
        "Loading semantic model..."
    )

    semantic_model_id = (
        get_semantic_model_id(
            cursor,
            semantic_model_name,
            fabric_model_id,
        )
    )

    print(
        f"SemanticModelID: "
        f"{semantic_model_id}"
    )

    # =======================================================================
    # 5. SEMANTIC TABLES
    # =======================================================================

    print()
    print(
        "Loading semantic tables..."
    )

    semantic_table_lookup = (
        load_semantic_tables(
            cursor,
            semantic_model_id,
            tables,
        )
    )

    # =======================================================================
    # 6. SOURCE TABLE LINKS
    # =======================================================================

    print(
        "Linking semantic tables to SQL tables..."
    )

    source_table_lookup = (
        build_source_table_lookup(
            cursor,
            database_id,
        )
    )

    load_semantic_table_sources(
        cursor,
        semantic_table_lookup,
        source_table_lookup,
    )

    # =======================================================================
    # 7. SEMANTIC COLUMNS
    # =======================================================================

    print(
        "Loading semantic columns..."
    )

    semantic_column_lookup = (
        load_semantic_columns(
            cursor,
            semantic_table_lookup,
            columns,
        )
    )

    # =======================================================================
    # 8. SOURCE COLUMN LINKS
    # =======================================================================

    print(
        "Linking semantic columns to SQL columns..."
    )

    source_column_lookup = (
        build_source_column_lookup(
            cursor,
            database_id,
        )
    )

    load_semantic_column_sources(
        cursor,
        semantic_column_lookup,
        semantic_table_lookup,
        source_column_lookup,
    )

    # =======================================================================
    # 9. MEASURES
    # =======================================================================

    print()
    print(
        "Loading measures and DAX definitions..."
    )

    load_measures(
        cursor,
        semantic_model_id,
        tables,
        semantic_table_lookup,
        measures,
    )

    # =======================================================================
    # 10. MEASURE DEPENDENCIES
    # =======================================================================

    print()
    print(
        "Loading measure dependencies..."
    )

    measure_dependencies_inserted = (
        load_measure_dependencies(
            cursor,
            semantic_model_id,
            semantic_table_lookup,
            semantic_column_lookup,
            measures,
        )
    )

    print(
        f"Measure dependencies loaded: "
        f"{measure_dependencies_inserted}"
    )

    # =======================================================================
    # 11. RELATIONSHIPS
    # =======================================================================

    print(
        "Loading semantic relationships..."
    )

    load_semantic_relationships(
        cursor,
        semantic_model_id,
        semantic_table_lookup,
        semantic_column_lookup,
        relationships,
    )

    # =======================================================================
    # FINAL MODEL SUMMARY
    # =======================================================================

    print()
    print("-" * 70)
    print(
        f"COMPLETED: {semantic_model_name}"
    )
    print("-" * 70)

    print(
        f"Fabric Semantic Model ID: {fabric_model_id}"
    )

    print(
        f"Repository SemanticModelID: "
        f"{semantic_model_id}"
    )

    print(
        f"Tables extracted:   "
        f"{len(tables)}"
    )

    print(
        f"Columns extracted:  "
        f"{len(columns)}"
    )

    print(
        f"Measures extracted: "
        f"{len(measures)}"
    )

    print(
        f"Measure dependencies:"
        f" {measure_dependencies_inserted}"
    )

    print(
        f"Relationships:      "
        f"{len(relationships)}"
    )

    return {
        "name": semantic_model_name,
        "fabric_model_id": fabric_model_id,
        "semantic_model_id": semantic_model_id,
        "tables": len(tables),
        "columns": len(columns),
        "measures": len(measures),
        "dependencies": measure_dependencies_inserted,
        "relationships": len(relationships),
    }


# ===========================================================================
# MAIN
# ===========================================================================


def main():

    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
    )

    print("=" * 70)
    print("FABRIC SEMANTIC MODEL METADATA EXTRACTOR")
    print("=" * 70)

    repository_connection = None

    try:

        # ===================================================================
        # 1. CREATE FABRIC CLIENT
        # ===================================================================

        client = FabricClient()

        # ===================================================================
        # 2. DISCOVER ALL SEMANTIC MODELS
        # ===================================================================

        print()
        print("=" * 70)
        print("DISCOVERING SEMANTIC MODELS")
        print("=" * 70)

        semantic_models = client.get_items_by_type(
            WORKSPACE_ID,
            "SemanticModel",
        )

        print(
            f"Semantic models discovered: "
            f"{len(semantic_models)}"
        )

        if not semantic_models:

            raise RuntimeError(
                "No semantic models were found in the "
                "specified Fabric workspace."
            )

        print()

        for index, model in enumerate(
            semantic_models,
            start=1,
        ):

            print(
                f"  {index}. "
                f"{model.get('displayName')} "
                f"| ID: {model.get('id')}"
            )

        # ===================================================================
        # 3. CONNECT TO METADATA REPOSITORY
        # ===================================================================

        print()
        print("=" * 70)
        print("CONNECTING TO METADATA REPOSITORY")
        print("=" * 70)

        print()
        print(
            "A Microsoft Entra login window should appear..."
        )

        repository_connection = (
            connect_to_fabric_warehouse(
                DEFAULT_DRIVER,
                FABRIC_SQL_SERVER,
                FABRIC_SQL_DATABASE,
            )
        )

        cursor = (
            repository_connection.cursor()
        )

        print()
        print(
            "Connected to MetadataRepository successfully."
        )

        # ===================================================================
        # 4. GET SOURCE DATABASE ID
        # ===================================================================

        cursor.execute(
            """
            SELECT DatabaseID
            FROM dbo.MetadataDatabase
            WHERE DatabaseName = ?
              AND ServerName = ?
            """,
            SOURCE_DATABASE,
            SOURCE_SERVER,
        )

        result = cursor.fetchone()

        if not result:

            raise RuntimeError(
                "Source database was not found in "
                "MetadataDatabase. "
                "Run the SQL metadata extractor first."
            )

        database_id = int(
            result[0]
        )

        print()
        print(
            f"Using source DatabaseID: {database_id}"
        )

        # ===================================================================
        # 5. PROCESS EVERY SEMANTIC MODEL
        # ===================================================================

        results = []
        successful = 0
        failed = 0

        for model in semantic_models:

            fabric_model_id = model.get("id")
            semantic_model_name = model.get(
                "displayName"
            )

            if not fabric_model_id:

                logging.warning(
                    "Skipping semantic model with no ID: %s",
                    model,
                )

                failed += 1
                continue

            if not semantic_model_name:

                logging.warning(
                    "Skipping semantic model with no display name: %s",
                    model,
                )

                failed += 1
                continue

            try:

                result = process_semantic_model(
                    client=client,
                    cursor=cursor,
                    database_id=database_id,
                    fabric_model_id=fabric_model_id,
                    semantic_model_name=semantic_model_name,
                )

                results.append(result)

                successful += 1

            except Exception:

                failed += 1

                logging.exception(
                    "Failed to extract semantic model: %s",
                    semantic_model_name,
                )

                print()
                print(
                    f"ERROR extracting semantic model: "
                    f"{semantic_model_name}"
                )

                # Continue with the remaining semantic models.
                continue

        # ===================================================================
        # 6. FINAL SUMMARY
        # ===================================================================

        print()
        print("=" * 70)
        print(
            "SEMANTIC MODEL EXTRACTION COMPLETED"
        )
        print("=" * 70)

        print(
            f"Semantic models discovered: "
            f"{len(semantic_models)}"
        )

        print(
            f"Semantic models successful:  "
            f"{successful}"
        )

        print(
            f"Semantic models failed:      "
            f"{failed}"
        )

        print()

        for result in results:

            print(
                f"Semantic model: "
                f"{result['name']}"
            )

            print(
                f"  Fabric Model ID: "
                f"{result['fabric_model_id']}"
            )

            print(
                f"  Repository ID:    "
                f"{result['semantic_model_id']}"
            )

            print(
                f"  Tables:           "
                f"{result['tables']}"
            )

            print(
                f"  Columns:          "
                f"{result['columns']}"
            )

            print(
                f"  Measures:         "
                f"{result['measures']}"
            )

            print(
                f"  Dependencies:     "
                f"{result['dependencies']}"
            )

            print(
                f"  Relationships:    "
                f"{result['relationships']}"
            )

            print()

        if failed > 0:

            logging.warning(
                "%d semantic model(s) failed extraction.",
                failed,
            )

        if successful == 0:

            raise RuntimeError(
                "No semantic models were extracted successfully."
            )

        print(
            "Metadata successfully loaded into "
            "MetadataRepository."
        )

        return 0

    except Exception as exc:

        logging.exception(
            "Semantic model extraction failed"
        )

        print()
        print(
            "ERROR:",
            str(exc),
        )

        return 1

    finally:

        if repository_connection:

            repository_connection.close()

            logging.info(
                "Closed MetadataRepository connection."
            )


# ===========================================================================
# ENTRY POINT
# ===========================================================================


if __name__ == "__main__":

    raise SystemExit(
        main()
    )