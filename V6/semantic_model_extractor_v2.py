import base64
import json
import logging
import re
import sys

from pathlib import Path
from collections import defaultdict

import pyodbc


# ============================================================================
# PROJECT PATH
# ============================================================================

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from config.config import DEFAULT_DRIVER
from src.fabric.client import FabricClient


# ============================================================================
# CONFIGURATION
# ============================================================================

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

WORKSPACE_CONFIG_FILE = (
    ROOT_DIR / "config" / "workspaces.json"
)


# ============================================================================
# METADATA REPOSITORY
# ============================================================================

# Fabric Warehouse SQL endpoint
FABRIC_SQL_SERVER = (
    "j7mjaqg22d2ujb27llpciiyism-7jnw46tiqcde5cpv233ctk345u.datawarehouse.fabric.microsoft.com"
)

# Fabric Warehouse database name
FABRIC_SQL_DATABASE = "MetadataRepository"


# ============================================================================
# WORKSPACE CONFIGURATION
# ============================================================================

def load_enabled_workspaces():
    """
    Load enabled Fabric workspaces from config/workspaces.json.

    Expected format:

    {
        "workspaces": [
            {
                "workspace_id": "...",
                "workspace_name": "...",
                "enabled": true
            }
        ]
    }

    Only workspaces with enabled=true are returned.
    """

    if not WORKSPACE_CONFIG_FILE.exists():

        raise FileNotFoundError(
            "Workspace configuration file not found: "
            f"{WORKSPACE_CONFIG_FILE}"
        )

    try:

        with open(
            WORKSPACE_CONFIG_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            config = json.load(file)

    except json.JSONDecodeError as exc:

        raise RuntimeError(
            "Invalid JSON in workspace configuration file: "
            f"{WORKSPACE_CONFIG_FILE}"
        ) from exc

    workspaces = config.get(
        "workspaces",
        [],
    )

    if not isinstance(
        workspaces,
        list,
    ):

        raise RuntimeError(
            "'workspaces' must be a list in "
            f"{WORKSPACE_CONFIG_FILE}"
        )

    enabled_workspaces = []

    for workspace in workspaces:

        if not isinstance(
            workspace,
            dict,
        ):

            logging.warning(
                "Ignoring invalid workspace entry: %s",
                workspace,
            )

            continue

        workspace_id = workspace.get(
            "workspace_id"
        )

        workspace_name = workspace.get(
            "workspace_name"
        )

        enabled = workspace.get(
            "enabled",
            True,
        )

        if not workspace_id:

            logging.warning(
                "Ignoring workspace without workspace_id: %s",
                workspace,
            )

            continue

        if not workspace_name:

            logging.warning(
                "Ignoring workspace without workspace_name: %s",
                workspace_id,
            )

            continue

        if not enabled:

            logging.info(
                "Workspace disabled: %s | %s",
                workspace_name,
                workspace_id,
            )

            continue

        enabled_workspaces.append(
            {
                "workspace_id": workspace_id,
                "workspace_name": workspace_name,
            }
        )

    if not enabled_workspaces:

        raise RuntimeError(
            "No enabled workspaces found in "
            f"{WORKSPACE_CONFIG_FILE}"
        )

    return enabled_workspaces


# ============================================================================
# READ / WRITE FABRIC WAREHOUSE CONNECTION
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
        # Forces ODBC Driver 18 to bind long text parameters as
        # varchar(max) instead of falling back to the legacy
        # SQL_LONGVARCHAR -> text/ntext path, which is incompatible
        # with UTF-8 collations (Latin1_General_100_BIN2_UTF8).
        "LongAsMax=yes;"
    )


def _decode_utf8_column(raw_bytes):
    """
    Output converter for long UTF-8-collation varchar(max) columns
    on READ. Receives the FULLY REASSEMBLED raw bytes for the column
    value (pyodbc has already looped over all internal SQLGetData
    chunks and concatenated them before calling this function), so
    decoding here -- once -- avoids a pyodbc bug where
    connection.setdecoding(SQL_CHAR, "utf-8") can decode individual
    internal chunks separately and fail with "unexpected end of
    data" whenever a multi-byte UTF-8 character (e.g. Δ = 0xCE 0x94)
    falls across a chunk boundary.
    """

    if raw_bytes is None:
        return None

    return raw_bytes.decode("utf-8")


def connect_to_fabric_warehouse(
    driver: str,
    server: str,
    database: str,
) -> pyodbc.Connection:

    connection_string = (
        get_fabric_connection_string(
            driver,
            server,
            database,
        )
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
    # Forcing SQL_C_CHAR/UTF-8 binding on outgoing parameters causes
    # the ODBC driver to reinterpret those bytes through the
    # client's ANSI codepage (e.g. cp1252) before converting them
    # into the UTF-8-collation column -- double-encoding characters
    # like Δ (0xCE 0x94) into "Î\x94". Leaving encoding unset lets
    # pyodbc use its default SQL_C_WCHAR / UTF-16LE binding for
    # outgoing strings, which the driver converts unambiguously into
    # the UTF-8-collation varchar(max) columns. Combined with
    # LongAsMax=yes in the connection string, this writes long,
    # non-ASCII DAX/M expressions correctly.

    return connection


# ============================================================================
# TMDL EXTRACTOR
# ============================================================================

class SemanticModelExtractor:
    """
    V6.3 Semantic Model Metadata Extractor.

    Responsibilities:

        - Extract semantic models
        - Extract semantic tables
        - Classify tables
        - Extract columns
        - Detect calculated columns
        - Extract measures
        - Extract DAX
        - Extract Power Query / M
        - Extract source mappings
        - Extract source columns
        - Detect Power Query renames
        - Resolve rename chains
        - Extract calculated tables
        - Extract semantic dependencies
        - Extract measure dependencies
        - Extract table dependencies
        - Extract relationships
        - Detect hidden objects

    This class is extraction-only.

    Repository persistence is handled separately by
    MetadataRepositoryWriter.
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

    # ========================================================================
    # PAYLOAD
    # ========================================================================

    @staticmethod
    def _decode_part(part):

        payload = part.get("payload")

        if not payload:
            return ""

        payload_type = part.get("payloadType")

        if payload_type != "InlineBase64":
            return ""

        try:

            decoded = base64.b64decode(
                payload
            )

            return decoded.decode(
                "utf-8",
                errors="replace",
            )

        except Exception:

            logging.exception(
                "Failed to decode TMDL part: %s",
                part.get("path"),
            )

            return ""

    # ========================================================================
    # TABLES
    # ========================================================================

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

            content = self._decode_part(
                part
            )

            table_name = (
                self._extract_table_name(
                    content
                )
            )

            if not table_name:
                continue

            table_type = (
                self._classify_table(
                    table_name,
                    content,
                )
            )

            is_hidden = (
                self._extract_property(
                    content,
                    "isHidden",
                )
            )

            tables.append(
                {
                    "table_name": table_name,
                    "table_type": table_type,
                    "definition_path": path,
                    "is_hidden": is_hidden,
                    "content": content,
                }
            )

        return tables

    # ========================================================================
    # TABLE CLASSIFICATION
    # ========================================================================

    @staticmethod
    def _classify_table(
        table_name,
        content,
    ):

        lower_name = table_name.lower()

        lower_content = content.lower()

        if table_name.startswith(
            "DateTableTemplate_"
        ):
            return "Date Template"

        if table_name.startswith(
            "LocalDateTable_"
        ):
            return "Auto Date"

        if lower_name in {
            "dax_measures",
            "measures",
            "measure table",
        }:
            return "Measure Table"

        if re.search(
            r"(?im)^\s*partition\s+.+?=\s*calculated\b",
            content,
        ):
            return "Calculated"

        if (
            "tablekind = calculated"
            in lower_content
        ):
            return "Calculated"

        if (
            "calculatedtable"
            in lower_content
        ):
            return "Calculated"

        return "Source"

    # ========================================================================
    # COLUMNS
    # ========================================================================

    def extract_columns(
        self,
        tables,
    ):

        columns = []

        for table in tables:

            table_name = table[
                "table_name"
            ]

            content = table[
                "content"
            ]

            path = table[
                "definition_path"
            ]

            lines = content.splitlines()

            index = 0

            while index < len(lines):

                stripped = lines[
                    index
                ].strip()

                if not stripped.startswith(
                    "column "
                ):
                    index += 1
                    continue

                definition = (
                    stripped[
                        len("column "):
                    ].strip()
                )

                column_name = (
                    self._extract_object_name(
                        definition
                    )
                )

                if not column_name:

                    index += 1

                    continue

                remainder = (
                    self._remove_object_name(
                        definition
                    )
                )

                calculated = False

                expression = None

                if remainder.startswith(
                    "="
                ):

                    calculated = True

                    expression = (
                        remainder[
                            1:
                        ].strip()
                    )

                    expression = (
                        self._collect_multiline_expression(
                            lines,
                            index,
                            expression,
                        )
                    )

                object_block = (
                    self._get_object_block(
                        lines,
                        index,
                    )
                )

                source_column = (
                    self._extract_property(
                        object_block,
                        "sourceColumn",
                    )
                )

                data_type = (
                    self._extract_property(
                        object_block,
                        "dataType",
                    )
                )

                is_hidden = (
                    self._extract_property(
                        object_block,
                        "isHidden",
                    )
                )

                columns.append(
                    {
                        "table_name": table_name,
                        "column_name": column_name,
                        "column_type": (
                            "Calculated"
                            if calculated
                            else "Source"
                        ),
                        "expression": expression,
                        "source_column": source_column,
                        "data_type": data_type,
                        "is_hidden": is_hidden,
                        "definition_path": path,
                    }
                )

                index += 1

        return columns

    # ========================================================================
    # MEASURES
    # ========================================================================

    def extract_measures(
        self,
        tables,
    ):

        measures = []

        for table in tables:

            table_name = table[
                "table_name"
            ]

            path = table[
                "definition_path"
            ]

            content = table[
                "content"
            ]

            lines = content.splitlines()

            index = 0

            while index < len(lines):

                stripped = lines[
                    index
                ].strip()

                if not stripped.startswith(
                    "measure "
                ):
                    index += 1
                    continue

                definition = (
                    stripped[
                        len("measure "):
                    ].strip()
                )

                match = re.match(
                    r"'([^']+)'\s*=\s*(.*)",
                    definition,
                )

                if match:

                    measure_name = (
                        match.group(1)
                    )

                    expression = (
                        match.group(2)
                    )

                else:

                    match = re.match(
                        r"([^\s=]+)\s*=\s*(.*)",
                        definition,
                    )

                    if not match:

                        index += 1

                        continue

                    measure_name = (
                        match.group(1)
                    )

                    expression = (
                        match.group(2)
                    )

                expression = (
                    self._collect_multiline_expression(
                        lines,
                        index,
                        expression,
                    )
                )

                block = (
                    self._get_object_block(
                        lines,
                        index,
                    )
                )

                is_hidden = (
                    self._extract_property(
                        block,
                        "isHidden",
                    )
                )

                measures.append(
                    {
                        "table_name": table_name,
                        "measure_name": measure_name,
                        "expression": expression,
                        "definition_path": path,
                        "is_hidden": is_hidden,
                    }
                )

                index += 1

        return measures

    # ========================================================================
    # SOURCE MAPPINGS
    # ========================================================================

    def extract_source_mappings(
        self,
        tables,
    ):

        mappings = []

        for table in tables:

            if table[
                "table_type"
            ] != "Source":

                continue

            table_name = table[
                "table_name"
            ]

            content = table[
                "content"
            ]

            m_expression = (
                self._extract_m_expression(
                    content
                )
            )

            source_info = (
                self._extract_source_information(
                    m_expression
                )
            )

            rename_operations = (
                self._extract_rename_operations(
                    m_expression
                )
            )

            rename_map = (
                self._build_final_rename_map(
                    rename_operations
                )
            )

            mappings.append(
                {
                    "semantic_table": table_name,
                    "source_type": source_info[
                        "source_type"
                    ],
                    "server": source_info[
                        "server"
                    ],
                    "database": source_info[
                        "database"
                    ],
                    "workspace": source_info[
                        "workspace"
                    ],
                    "warehouse": source_info[
                        "warehouse"
                    ],
                    "schema": source_info[
                        "schema"
                    ],
                    "source_table": source_info[
                        "source_table"
                    ],
                    "m_expression": m_expression,
                    "rename_operations": rename_operations,
                    "rename_map": rename_map,
                }
            )

        return mappings

    # ========================================================================
    # SOURCE INFORMATION
    # ========================================================================

    @staticmethod
    def _extract_source_information(
        m_expression,
    ):

        result = {
            "source_type": "UNKNOWN",
            "server": None,
            "database": None,
            "workspace": None,
            "warehouse": None,
            "schema": None,
            "source_table": None,
        }

        if not m_expression:
            return result

        # --------------------------------------------------------------------
        # SQL DATABASE
        # --------------------------------------------------------------------

        sql_match = re.search(
            r'Sql\.Database\s*'
            r'\(\s*'
            r'"([^"]+)"'
            r'\s*,\s*'
            r'"([^"]+)"'
            r'\s*\)',
            m_expression,
            re.IGNORECASE,
        )

        if sql_match:

            result[
                "source_type"
            ] = "SQL"

            result[
                "server"
            ] = sql_match.group(1)

            result[
                "database"
            ] = sql_match.group(2)

        # --------------------------------------------------------------------
        # FABRIC WAREHOUSE
        # --------------------------------------------------------------------

        warehouse_match = re.search(
            r'Fabric\.Warehouse\s*'
            r'\(\s*'
            r'"([^"]+)"'
            r'\s*,\s*'
            r'"([^"]+)"',
            m_expression,
            re.IGNORECASE,
        )

        if warehouse_match:

            result[
                "source_type"
            ] = "FABRIC_WAREHOUSE"

            result[
                "workspace"
            ] = warehouse_match.group(1)

            result[
                "warehouse"
            ] = warehouse_match.group(2)

        # --------------------------------------------------------------------
        # GENERIC FABRIC WAREHOUSE REFERENCE
        # --------------------------------------------------------------------

        if (
            result[
                "source_type"
            ]
            == "UNKNOWN"
        ):

            if (
                "warehouse"
                in m_expression.lower()
            ):

                result[
                    "source_type"
                ] = "FABRIC_WAREHOUSE"

        # --------------------------------------------------------------------
        # NAVIGATION
        # --------------------------------------------------------------------

        navigation_match = re.search(
            r'\[\s*Schema\s*=\s*"([^"]+)"'
            r'\s*,\s*Item\s*=\s*"([^"]+)"',
            m_expression,
            re.IGNORECASE,
        )

        if navigation_match:

            result[
                "schema"
            ] = navigation_match.group(1)

            result[
                "source_table"
            ] = navigation_match.group(2)

        # --------------------------------------------------------------------
        # ALTERNATIVE ITEM-ONLY NAVIGATION
        # --------------------------------------------------------------------

        if not result[
            "source_table"
        ]:

            item_match = re.search(
                r'\[\s*Item\s*=\s*"([^"]+)"',
                m_expression,
                re.IGNORECASE,
            )

            if item_match:

                result[
                    "source_table"
                ] = item_match.group(1)

        return result

    # ========================================================================
    # M EXPRESSION
    # ========================================================================

    @staticmethod
    def _extract_m_expression(
        content,
    ):

        match = re.search(
            r"(?ims)"
            r"^\s*partition\s+.+?=\s*m\s*"
            r"(.*?)"
            r"(?=^\s*(?:annotation|partition)\b|\Z)",
            content,
        )

        if match:

            return match.group(1).strip()

        match = re.search(
            r"(?ims)"
            r"\blet\s+"
            r"(.*?)"
            r"(?=^\s*annotation\b|\Z)",
            content,
        )

        if match:

            return (
                "let\n"
                + match.group(1).strip()
            )

        return ""

    # ========================================================================
    # RENAME OPERATIONS
    # ========================================================================

    @staticmethod
    def _extract_rename_operations(
        m_expression,
    ):

        operations = []

        if not m_expression:
            return operations

        pattern = re.compile(
            r"Table\.RenameColumns"
            r"\s*\("
            r".*?"
            r"\{"
            r"(.*?)"
            r"\}"
            r"\s*\)",
            re.IGNORECASE | re.DOTALL,
        )

        for match in pattern.finditer(
            m_expression
        ):

            pairs_text = match.group(1)

            pairs = re.findall(
                r'\{\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\}',
                pairs_text,
            )

            for original, semantic in pairs:

                operations.append(
                    {
                        "original_column": original,
                        "new_column": semantic,
                        "transformation": (
                            "Table.RenameColumns"
                        ),
                    }
                )

        return operations

    # ========================================================================
    # FINAL RENAME MAP
    # ========================================================================

    @staticmethod
    def _build_final_rename_map(
        operations,
    ):

        if not operations:
            return {}

        final_to_original = {}

        for operation in operations:

            original = operation[
                "original_column"
            ]

            new_name = operation[
                "new_column"
            ]

            physical = (
                final_to_original.get(
                    original,
                    original,
                )
            )

            final_to_original[
                new_name
            ] = physical

        return final_to_original

    # ========================================================================
    # CALCULATED TABLES
    # ========================================================================

    def extract_calculated_tables(
        self,
        tables,
    ):

        results = []

        for table in tables:

            if table[
                "table_type"
            ] != "Calculated":

                continue

            expression = (
                self._extract_calculated_expression(
                    table[
                        "content"
                    ]
                )
            )

            results.append(
                {
                    "table_name": table[
                        "table_name"
                    ],
                    "expression": expression,
                    "definition_path": table[
                        "definition_path"
                    ],
                }
            )

        return results

    @staticmethod
    def _extract_calculated_expression(
        content,
    ):

        match = re.search(
            r"(?ims)"
            r"^\s*partition\s+.+?=\s*calculated\s*"
            r"(.*?)"
            r"(?=^\s*(?:annotation|partition)\b|\Z)",
            content,
        )

        if not match:
            return ""

        block = match.group(1).strip()

        block = re.sub(
            r"^\s*expression\s*=\s*",
            "",
            block,
            flags=re.IGNORECASE,
        )

        return block.strip()

    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================

    def extract_relationships(
        self,
    ):

        relationships = []

        relationship_part = None

        for part in self.parts:

            if (
                part.get("path")
                == "definition/relationships.tmdl"
            ):

                relationship_part = part

                break

        if not relationship_part:
            return relationships

        content = self._decode_part(
            relationship_part
        )

        lines = content.splitlines()

        current = None

        for line in lines:

            stripped = line.strip()

            if stripped.startswith(
                "relationship "
            ):

                if current:
                    relationships.append(
                        current
                    )

                relationship_id = (
                    stripped[
                        len("relationship "):
                    ].strip()
                )

                current = {
                    "relationship_id":
                        relationship_id,

                    "from_table":
                        None,

                    "from_column":
                        None,

                    "to_table":
                        None,

                    "to_column":
                        None,
                }

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

                if current:

                    current[
                        "from_table"
                    ] = table

                    current[
                        "from_column"
                    ] = column

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

                if current:

                    current[
                        "to_table"
                    ] = table

                    current[
                        "to_column"
                    ] = column

        if current:
            relationships.append(
                current
            )

        return relationships

    # ========================================================================
    # HELPERS
    # ========================================================================

    @staticmethod
    def _extract_table_name(
        content,
    ):

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
    def _extract_object_name(
        value,
    ):

        value = value.strip()

        if value.startswith("'"):

            match = re.match(
                r"'([^']+)'",
                value,
            )

            if match:
                return match.group(1)

        parts = value.split()

        if parts:

            return parts[
                0
            ].strip("'")

        return None

    @staticmethod
    def _remove_object_name(
        value,
    ):

        value = value.strip()

        if value.startswith("'"):

            match = re.match(
                r"'[^']+'\s*(.*)$",
                value,
            )

            if match:

                return match.group(
                    1
                ).strip()

        match = re.match(
            r"[^\s]+\s*(.*)$",
            value,
        )

        if match:

            return match.group(
                1
            ).strip()

        return ""

    @staticmethod
    def _split_table_column(
        value,
    ):

        value = value.strip()

        match = re.match(
            r"'([^']+)'\.\[([^\]]+)\]",
            value,
        )

        if match:

            return (
                match.group(1),
                match.group(2),
            )

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

            table, column = (
                value.rsplit(
                    ".",
                    1,
                )
            )

            return (
                table.strip("'"),
                column.strip("[]"),
            )

        return (
            None,
            value.strip("[]"),
        )

    @staticmethod
    def _extract_property(
        content,
        property_name,
    ):

        if not content:
            return None

        pattern = re.compile(
            rf"(?im)^\s*{re.escape(property_name)}\s*[:=]\s*(.+?)\s*$"
        )

        match = pattern.search(
            content
        )

        if not match:
            return None

        value = match.group(
            1
        ).strip()

        value = value.strip(
            '"'
        )

        return value

    @staticmethod
    def _get_object_block(
        lines,
        start_index,
    ):

        block = []

        index = start_index

        while index < len(lines):

            line = lines[
                index
            ]

            stripped = line.strip()

            if (
                index > start_index
                and stripped
                and not line.startswith(
                    " "
                )
                and not line.startswith(
                    "\t"
                )
            ):

                if (
                    stripped.startswith(
                        "column "
                    )
                    or stripped.startswith(
                        "measure "
                    )
                    or stripped.startswith(
                        "partition "
                    )
                    or stripped.startswith(
                        "table "
                    )
                ):
                    break

            block.append(line)

            index += 1

        return "\n".join(
            block
        )

    @staticmethod
    def _collect_multiline_expression(
        lines,
        index,
        initial_expression,
    ):

        expression_lines = []

        if initial_expression:

            expression_lines.append(
                initial_expression.strip()
            )

        next_index = index + 1

        while next_index < len(lines):

            next_line = lines[
                next_index
            ]

            stripped_next = (
                next_line.strip()
            )

            if not stripped_next:

                next_index += 1

                continue

            if stripped_next.startswith(
                "measure "
            ):
                break

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

            if re.match(
                r"^\s*\w+\s*[:=]",
                stripped_next,
            ):
                break

            if (
                not next_line.startswith(
                    " "
                )
                and not next_line.startswith(
                    "\t"
                )
            ):
                break

            expression_lines.append(
                stripped_next
            )

            next_index += 1

        return "\n".join(
            expression_lines
        ).strip()


# ============================================================================
# DAX DEPENDENCY ANALYZER
# ============================================================================

class DaxDependencyAnalyzer:

    TABLE_COLUMN_PATTERN = re.compile(
        r"'([^']+)'\s*\[\s*([^\]]+)\s*\]"
    )

    BARE_MEASURE_PATTERN = re.compile(
        r"(?<!['\w])"
        r"\[\s*([^\]]+)\s*\]"
    )

    @classmethod
    def analyze_expression(
        cls,
        expression,
    ):

        expression = (
            expression or ""
        )

        table_columns = []

        for match in cls.TABLE_COLUMN_PATTERN.finditer(
            expression
        ):

            table_columns.append(
                {
                    "table":
                        match.group(1),

                    "column":
                        match.group(2).strip(),
                }
            )

        measure_references = []

        for match in cls.BARE_MEASURE_PATTERN.finditer(
            expression
        ):

            name = match.group(
                1
            ).strip()

            if name:

                measure_references.append(
                    name
                )

        return {
            "table_columns":
                cls._deduplicate_dicts(
                    table_columns
                ),

            "measure_references":
                sorted(
                    set(
                        measure_references
                    ),
                    key=str.lower,
                ),
        }

    @classmethod
    def analyze_calculated_column(
        cls,
        column,
    ):

        return cls.analyze_expression(
            column.get(
                "expression"
            )
        )

    @classmethod
    def analyze_calculated_table(
        cls,
        table,
    ):

        return cls.analyze_expression(
            table.get(
                "expression"
            )
        )

    @staticmethod
    def _deduplicate_dicts(
        items,
    ):

        seen = set()

        result = []

        for item in items:

            key = (
                item.get("table"),
                item.get("column"),
            )

            if key in seen:
                continue

            seen.add(key)

            result.append(item)

        return result


# ============================================================================
# PHYSICAL REPOSITORY VALIDATOR
# ============================================================================

class RepositoryValidator:

    """
    Reads the existing physical metadata repository.

    Supports:

        server
        database
        schema
        table
        column

    No physical metadata is modified here.
    """

    def __init__(
        self,
        cursor,
    ):

        self.cursor = cursor

        self.tables = {}

        self.columns = defaultdict(
            set
        )

        self.loaded_databases = set()

    # ========================================================================
    # LOAD DATABASE
    # ========================================================================

    def load_source_metadata(
        self,
        database_name,
        server_name,
    ):

        database_key = (
            str(server_name).lower(),
            str(database_name).lower(),
        )

        if database_key in (
            self.loaded_databases
        ):

            return

        self.cursor.execute(
            """
            SELECT
                DatabaseID
            FROM dbo.MetadataDatabase
            WHERE LOWER(DatabaseName) = LOWER(?)
              AND LOWER(ServerName) = LOWER(?)
            """,
            database_name,
            server_name,
        )

        row = self.cursor.fetchone()

        if not row:

            logging.warning(
                "Physical database not found: %s / %s",
                server_name,
                database_name,
            )

            return

        database_id = int(
            row[0]
        )

        self.cursor.execute(
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

        for (
            table_id,
            schema_name,
            table_name,
        ) in self.cursor.fetchall():

            key = (
                str(server_name).lower(),
                str(database_name).lower(),
                str(schema_name).lower(),
                str(table_name).lower(),
            )

            self.tables[key] = {
                "table_id":
                    int(table_id),

                "server":
                    server_name,

                "database":
                    database_name,

                "schema":
                    schema_name,

                "table":
                    table_name,
            }

        self.cursor.execute(
            """
            SELECT
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

        for (
            schema_name,
            table_name,
            column_name,
        ) in self.cursor.fetchall():

            key = (
                str(server_name).lower(),
                str(database_name).lower(),
                str(schema_name).lower(),
                str(table_name).lower(),
            )

            self.columns[
                key
            ].add(
                str(column_name)
            )

        self.loaded_databases.add(
            database_key
        )

    # ========================================================================
    # TABLE
    # ========================================================================

    def find_table(
        self,
        server_name,
        database_name,
        schema_name,
        table_name,
    ):

        if not all(
            [
                server_name,
                database_name,
                schema_name,
                table_name,
            ]
        ):
            return None

        key = (
            str(server_name).lower(),
            str(database_name).lower(),
            str(schema_name).lower(),
            str(table_name).lower(),
        )

        return self.tables.get(
            key
        )

    # ========================================================================
    # COLUMN
    # ========================================================================

    def find_column(
        self,
        server_name,
        database_name,
        schema_name,
        table_name,
        column_name,
    ):

        table = self.find_table(
            server_name,
            database_name,
            schema_name,
            table_name,
        )

        if not table:
            return False

        key = (
            str(server_name).lower(),
            str(database_name).lower(),
            str(schema_name).lower(),
            str(table_name).lower(),
        )

        return any(
            str(column).lower()
            == str(column_name).lower()
            for column in self.columns.get(
                key,
                set(),
            )
        )


# ============================================================================
# METADATA REPOSITORY WRITER
# ============================================================================

class MetadataRepositoryWriter:

    """
    V6.3 repository persistence layer.

    Writes semantic metadata and workspace metadata.

    Existing physical metadata tables are read-only.

    Workspace-aware:
        Every semantic model is stored with its Fabric workspace ID/name.
    """

    def __init__(
        self,
        cursor,
    ):

        self.cursor = cursor

    # ========================================================================
    # WORKSPACE
    # ========================================================================

    def upsert_workspace(
        self,
        workspace_id,
        workspace_name,
        is_enabled=True,
    ):
        """
        Insert a workspace if it does not exist.

        Otherwise update the existing workspace.

        MetadataWorkspace schema:

            WorkspaceID
            WorkspaceName
            IsEnabled
        """

        if not workspace_id:
            raise ValueError(
                "workspace_id is required."
            )

        if not workspace_name:
            raise ValueError(
                "workspace_name is required."
            )

        self.cursor.execute(
            """
            SELECT
                WorkspaceID
            FROM dbo.MetadataWorkspace
            WHERE WorkspaceID = ?
            """,
            workspace_id,
        )

        row = self.cursor.fetchone()

        if row:

            self.cursor.execute(
                """
                UPDATE dbo.MetadataWorkspace
                SET
                    WorkspaceName = ?,
                    IsEnabled = ?
                WHERE WorkspaceID = ?
                """,
                workspace_name,
                1 if is_enabled else 0,
                workspace_id,
            )

            logging.info(
                "Workspace updated: %s | %s",
                workspace_name,
                workspace_id,
            )

        else:

            self.cursor.execute(
                """
                INSERT INTO dbo.MetadataWorkspace
                (
                    WorkspaceID,
                    WorkspaceName,
                    IsEnabled
                )
                VALUES
                (
                    ?, ?, ?
                )
                """,
                workspace_id,
                workspace_name,
                1 if is_enabled else 0,
            )

            logging.info(
                "Workspace inserted: %s | %s",
                workspace_name,
                workspace_id,
            )

    # ========================================================================
    # SEMANTIC MODEL
    # ========================================================================

    def get_or_create_model(
        self,
        model,
    ):

        fabric_model_id = (
            model["fabric_model_id"]
        )

        self.cursor.execute(
            """
            SELECT
                SemanticModelID
            FROM dbo.MetadataSemanticModel
            WHERE FabricModelID = ?
            """,
            fabric_model_id,
        )

        row = self.cursor.fetchone()

        if row:

            semantic_model_id = int(
                row[0]
            )

            self.cursor.execute(
                """
                UPDATE dbo.MetadataSemanticModel
                SET
                    ModelName = ?,
                    WorkspaceID = ?,
                    WorkspaceName = ?,
                    SourceType = ?
                WHERE SemanticModelID = ?
                """,
                model[
                    "semantic_model_name"
                ],
                model[
                    "workspace_id"
                ],
                model[
                    "workspace_name"
                ],
                "FABRIC_SEMANTIC_MODEL",
                semantic_model_id,
            )

            return semantic_model_id

        self.cursor.execute(
            """
            INSERT INTO dbo.MetadataSemanticModel
            (
                ModelName,
                WorkspaceID,
                WorkspaceName,
                FabricModelID,
                SourceType
            )
            VALUES
            (
                ?, ?, ?, ?, ?
            )
            """,
            model[
                "semantic_model_name"
            ],
            model[
                "workspace_id"
            ],
            model[
                "workspace_name"
            ],
            fabric_model_id,
            "FABRIC_SEMANTIC_MODEL",
        )

        self.cursor.execute(
            """
            SELECT
                SemanticModelID
            FROM dbo.MetadataSemanticModel
            WHERE FabricModelID = ?
            """,
            fabric_model_id,
        )

        row = self.cursor.fetchone()

        if not row:

            raise RuntimeError(
                "Failed to retrieve newly created SemanticModelID."
            )

        return int(
            row[0]
        )

    # ========================================================================
    # CLEAR MODEL CHILDREN
    # ========================================================================

    def clear_model_metadata(
        self,
        semantic_model_id,
    ):

        self.cursor.execute(
            """
            DELETE FROM dbo.MetadataSemanticRelationship
            WHERE SemanticModelID = ?
            """,
            semantic_model_id,
        )

        self.cursor.execute(
            """
            DELETE D
            FROM dbo.MetadataMeasureDependency D
            INNER JOIN dbo.MetadataMeasure M
                ON D.MeasureID = M.MeasureID
            WHERE M.SemanticModelID = ?
            """,
            semantic_model_id,
        )

        self.cursor.execute(
            """
            DELETE FROM dbo.MetadataMeasure
            WHERE SemanticModelID = ?
            """,
            semantic_model_id,
        )

        self.cursor.execute(
            """
            DELETE D
            FROM dbo.MetadataSemanticTableDependency D
            INNER JOIN dbo.MetadataSemanticTable T
                ON D.SemanticTableID = T.SemanticTableID
            WHERE T.SemanticModelID = ?
            """,
            semantic_model_id,
        )

        self.cursor.execute(
            """
            DELETE D
            FROM dbo.MetadataSemanticColumnDependency D
            INNER JOIN dbo.MetadataSemanticColumn C
                ON D.SourceSemanticColumnID =
                   C.SemanticColumnID
            INNER JOIN dbo.MetadataSemanticTable T
                ON C.SemanticTableID =
                   T.SemanticTableID
            WHERE T.SemanticModelID = ?
            """,
            semantic_model_id,
        )

        self.cursor.execute(
            """
            DELETE S
            FROM dbo.MetadataSemanticColumnSource S
            INNER JOIN dbo.MetadataSemanticColumn C
                ON S.SemanticColumnID =
                   C.SemanticColumnID
            INNER JOIN dbo.MetadataSemanticTable T
                ON C.SemanticTableID =
                   T.SemanticTableID
            WHERE T.SemanticModelID = ?
            """,
            semantic_model_id,
        )

        self.cursor.execute(
            """
            DELETE S
            FROM dbo.MetadataSemanticTableSource S
            INNER JOIN dbo.MetadataSemanticTable T
                ON S.SemanticTableID =
                   T.SemanticTableID
            WHERE T.SemanticModelID = ?
            """,
            semantic_model_id,
        )

        self.cursor.execute(
            """
            DELETE C
            FROM dbo.MetadataSemanticColumn C
            INNER JOIN dbo.MetadataSemanticTable T
                ON C.SemanticTableID =
                   T.SemanticTableID
            WHERE T.SemanticModelID = ?
            """,
            semantic_model_id,
        )

        self.cursor.execute(
            """
            DELETE FROM dbo.MetadataSemanticTable
            WHERE SemanticModelID = ?
            """,
            semantic_model_id,
        )

    # ========================================================================
    # INSERT TABLES
    # ========================================================================

    def insert_tables(
        self,
        semantic_model_id,
        tables,
    ):

        table_ids = {}

        for table in tables:

            self.cursor.execute(
                """
                INSERT INTO dbo.MetadataSemanticTable
                (
                    SemanticModelID,
                    TableName,
                    TableType,
                    DefinitionPath,
                    IsHidden
                )
                VALUES
                (
                    ?, ?, ?, ?, ?
                )
                """,
                semantic_model_id,
                table[
                    "table_name"
                ],
                table[
                    "table_type"
                ],
                table[
                    "definition_path"
                ],
                table[
                    "is_hidden"
                ],
            )

            self.cursor.execute(
                """
                SELECT
                    SemanticTableID
                FROM dbo.MetadataSemanticTable
                WHERE SemanticModelID = ?
                  AND TableName = ?
                """,
                semantic_model_id,
                table[
                    "table_name"
                ],
            )

            row = self.cursor.fetchone()

            if not row:

                raise RuntimeError(
                    "Could not retrieve SemanticTableID for "
                    f"{table['table_name']}"
                )

            table_ids[
                table[
                    "table_name"
                ].lower()
            ] = int(
                row[0]
            )

        return table_ids

    # ========================================================================
    # INSERT COLUMNS
    # ========================================================================

    def insert_columns(
        self,
        table_ids,
        columns,
    ):

        column_ids = {}

        for column in columns:

            table_key = column[
                "table_name"
            ].lower()

            semantic_table_id = (
                table_ids.get(
                    table_key
                )
            )

            if not semantic_table_id:
                continue

            self.cursor.execute(
                """
                INSERT INTO dbo.MetadataSemanticColumn
                (
                    SemanticTableID,
                    ColumnName,
                    DefinitionPath,
                    ColumnType,
                    IsHidden
                )
                VALUES
                (
                    ?, ?, ?, ?, ?
                )
                """,
                semantic_table_id,
                column[
                    "column_name"
                ],
                column[
                    "definition_path"
                ],
                column[
                    "column_type"
                ],
                column[
                    "is_hidden"
                ],
            )

            self.cursor.execute(
                """
                SELECT
                    SemanticColumnID
                FROM dbo.MetadataSemanticColumn
                WHERE SemanticTableID = ?
                  AND ColumnName = ?
                """,
                semantic_table_id,
                column[
                    "column_name"
                ],
            )

            row = self.cursor.fetchone()

            if not row:
                continue

            column_ids[
                (
                    column[
                        "table_name"
                    ].lower(),
                    column[
                        "column_name"
                    ].lower(),
                )
            ] = int(
                row[0]
            )

        return column_ids

    # ========================================================================
    # TABLE SOURCES
    # ========================================================================

    def insert_table_sources(
        self,
        table_ids,
        tables,
        source_mappings,
        repository_validator,
    ):

        mapping_by_table = {
            item[
                "semantic_table"
            ].lower(): item
            for item in source_mappings
        }

        for table in tables:

            semantic_table_id = (
                table_ids.get(
                    table[
                        "table_name"
                    ].lower()
                )
            )

            if not semantic_table_id:
                continue

            mapping = (
                mapping_by_table.get(
                    table[
                        "table_name"
                    ].lower()
                )
            )

            if not mapping:
                continue

            physical_table = (
                self._resolve_physical_table(
                    mapping,
                    repository_validator,
                )
            )

            if not physical_table:
                continue

            table_id = physical_table[
                "table_id"
            ]

            self.cursor.execute(
                """
                INSERT INTO dbo.MetadataSemanticTableSource
                (
                    SemanticTableID,
                    TableID,
                    ResolutionMethod,
                    SourceExpression
                )
                VALUES
                (
                    ?, ?, ?, ?
                )
                """,
                semantic_table_id,
                table_id,
                "SOURCE_MAPPING",
                mapping.get(
                    "m_expression"
                ),
            )

    # ========================================================================
    # COLUMN SOURCES
    # ========================================================================

    def insert_column_sources(
        self,
        column_ids,
        columns,
        source_mappings,
        repository_validator,
    ):

        mapping_by_table = {
            item[
                "semantic_table"
            ].lower(): item
            for item in source_mappings
        }

        for column in columns:

            if column[
                "column_type"
            ] != "Source":

                continue

            key = (
                column[
                    "table_name"
                ].lower(),
                column[
                    "column_name"
                ].lower(),
            )

            semantic_column_id = (
                column_ids.get(
                    key
                )
            )

            if not semantic_column_id:
                continue

            mapping = (
                mapping_by_table.get(
                    column[
                        "table_name"
                    ].lower()
                )
            )

            if not mapping:
                continue

            physical_column = (
                mapping.get(
                    "rename_map",
                    {},
                ).get(
                    column[
                        "column_name"
                    ],
                    column.get(
                        "source_column"
                    )
                    or column[
                        "column_name"
                    ],
                )
            )

            physical = (
                self._resolve_physical_column(
                    mapping,
                    physical_column,
                    repository_validator,
                )
            )

            if not physical:
                continue

            self.cursor.execute(
                """
                INSERT INTO dbo.MetadataSemanticColumnSource
                (
                    SemanticColumnID,
                    ColumnID,
                    ResolutionMethod,
                    SourceExpression
                )
                VALUES
                (
                    ?, ?, ?, ?
                )
                """,
                semantic_column_id,
                physical[
                    "column_id"
                ],
                "POWER_QUERY_MAPPING",
                mapping.get(
                    "m_expression"
                ),
            )

    # ========================================================================
    # COLUMN DEPENDENCIES
    # ========================================================================

    def insert_column_dependencies(
        self,
        columns,
        column_ids,
    ):

        for column in columns:

            if column[
                "column_type"
            ] != "Calculated":

                continue

            source_id = column_ids.get(
                (
                    column[
                        "table_name"
                    ].lower(),
                    column[
                        "column_name"
                    ].lower(),
                )
            )

            if not source_id:
                continue

            dependencies = (
                DaxDependencyAnalyzer
                .analyze_calculated_column(
                    column
                )
            )

            for dependency in dependencies[
                "table_columns"
            ]:

                target_table_id = (
                    self._find_table_id(
                        column_ids,
                        dependency[
                            "table"
                        ],
                    )
                )

                target_column_id = (
                    self._find_column_id(
                        column_ids,
                        dependency[
                            "table"
                        ],
                        dependency[
                            "column"
                        ],
                    )
                )

                self.cursor.execute(
                    """
                    INSERT INTO dbo.MetadataSemanticColumnDependency
                    (
                        SourceSemanticColumnID,
                        TargetSemanticTableID,
                        TargetSemanticColumnID,
                        DependencyType,
                        DependencyExpression
                    )
                    VALUES
                    (
                        ?, ?, ?, ?, ?
                    )
                    """,
                    source_id,
                    target_table_id,
                    target_column_id,
                    "CALCULATED_COLUMN",
                    column.get(
                        "expression"
                    ),
                )

    # ========================================================================
    # MEASURES
    # ========================================================================

    def insert_measures(
        self,
        semantic_model_id,
        table_ids,
        measures,
    ):

        measure_ids = {}

        for measure in measures:

            semantic_table_id = (
                table_ids.get(
                    measure[
                        "table_name"
                    ].lower()
                )
            )

            self.cursor.execute(
                """
                INSERT INTO dbo.MetadataMeasure
                (
                    SemanticModelID,
                    SemanticTableID,
                    MeasureName,
                    DAXExpression,
                    DefinitionPath,
                    IsHidden
                )
                VALUES
                (
                    ?, ?, ?, ?, ?, ?
                )
                """,
                semantic_model_id,
                semantic_table_id,
                measure[
                    "measure_name"
                ],
                measure[
                    "expression"
                ],
                measure[
                    "definition_path"
                ],
                measure[
                    "is_hidden"
                ],
            )

            self.cursor.execute(
                """
                SELECT
                    MeasureID
                FROM dbo.MetadataMeasure
                WHERE SemanticModelID = ?
                  AND MeasureName = ?
                """,
                semantic_model_id,
                measure[
                    "measure_name"
                ],
            )

            row = self.cursor.fetchone()

            if row:

                measure_ids[
                    measure[
                        "measure_name"
                    ].lower()
                ] = int(
                    row[0]
                )

        return measure_ids

    # ========================================================================
    # MEASURE DEPENDENCIES
    # ========================================================================

    def insert_measure_dependencies(
        self,
        measures,
        measure_ids,
        table_ids,
        column_ids,
    ):

        for measure in measures:

            measure_id = measure_ids.get(
                measure[
                    "measure_name"
                ].lower()
            )

            if not measure_id:
                continue

            dependencies = (
                DaxDependencyAnalyzer
                .analyze_expression(
                    measure[
                        "expression"
                    ]
                )
            )

            for dependency in dependencies[
                "table_columns"
            ]:

                target_table_id = (
                    self._find_table_id(
                        table_ids,
                        dependency[
                            "table"
                        ],
                    )
                )

                target_column_id = (
                    self._find_column_id(
                        column_ids,
                        dependency[
                            "table"
                        ],
                        dependency[
                            "column"
                        ],
                    )
                )

                self.cursor.execute(
                    """
                    INSERT INTO dbo.MetadataMeasureDependency
                    (
                        MeasureID,
                        SemanticTableID,
                        SemanticColumnID,
                        MeasureDependencyID,
                        DependencyType,
                        DependencyExpression
                    )
                    VALUES
                    (
                        ?, ?, ?, ?, ?, ?
                    )
                    """,
                    measure_id,
                    target_table_id,
                    target_column_id,
                    None,
                    "DAX_COLUMN",
                    measure[
                        "expression"
                    ],
                )

            for measure_reference in dependencies[
                "measure_references"
            ]:

                target_measure_id = (
                    measure_ids.get(
                        measure_reference.lower()
                    )
                )

                self.cursor.execute(
                    """
                    INSERT INTO dbo.MetadataMeasureDependency
                    (
                        MeasureID,
                        SemanticTableID,
                        SemanticColumnID,
                        MeasureDependencyID,
                        DependencyType,
                        DependencyExpression
                    )
                    VALUES
                    (
                        ?, ?, ?, ?, ?, ?
                    )
                    """,
                    measure_id,
                    None,
                    None,
                    target_measure_id,
                    "DAX_MEASURE",
                    measure[
                        "expression"
                    ],
                )

    # ========================================================================
    # TABLE DEPENDENCIES
    # ========================================================================

    def insert_table_dependencies(
        self,
        calculated_tables,
        table_ids,
        column_ids,
    ):

        for table in calculated_tables:

            source_table_id = table_ids.get(
                table[
                    "table_name"
                ].lower()
            )

            if not source_table_id:
                continue

            dependencies = (
                DaxDependencyAnalyzer
                .analyze_calculated_table(
                    table
                )
            )

            for dependency in dependencies[
                "table_columns"
            ]:

                target_table_id = (
                    self._find_table_id(
                        table_ids,
                        dependency[
                            "table"
                        ],
                    )
                )

                target_column_id = (
                    self._find_column_id(
                        column_ids,
                        dependency[
                            "table"
                        ],
                        dependency[
                            "column"
                        ],
                    )
                )

                self.cursor.execute(
                    """
                    INSERT INTO dbo.MetadataSemanticTableDependency
                    (
                        SemanticTableID,
                        TargetSemanticTableID,
                        TargetSemanticColumnID,
                        DependencyType,
                        DependencyExpression
                    )
                    VALUES
                    (
                        ?, ?, ?, ?, ?
                    )
                    """,
                    source_table_id,
                    target_table_id,
                    target_column_id,
                    "CALCULATED_TABLE",
                    table[
                        "expression"
                    ],
                )

    # ========================================================================
    # RELATIONSHIPS
    # ========================================================================

    def insert_relationships(
        self,
        semantic_model_id,
        table_ids,
        column_ids,
        relationships,
    ):

        for relationship in relationships:

            from_table_id = (
                self._find_table_id(
                    table_ids,
                    relationship[
                        "from_table"
                    ],
                )
            )

            from_column_id = (
                self._find_column_id(
                    column_ids,
                    relationship[
                        "from_table"
                    ],
                    relationship[
                        "from_column"
                    ],
                )
            )

            to_table_id = (
                self._find_table_id(
                    table_ids,
                    relationship[
                        "to_table"
                    ],
                )
            )

            to_column_id = (
                self._find_column_id(
                    column_ids,
                    relationship[
                        "to_table"
                    ],
                    relationship[
                        "to_column"
                    ],
                )
            )

            if not all(
                [
                    from_table_id,
                    from_column_id,
                    to_table_id,
                    to_column_id,
                ]
            ):
                continue

            self.cursor.execute(
                """
                INSERT INTO dbo.MetadataSemanticRelationship
                (
                    SemanticModelID,
                    FromTableID,
                    FromColumnID,
                    ToTableID,
                    ToColumnID
                )
                VALUES
                (
                    ?, ?, ?, ?, ?
                )
                """,
                semantic_model_id,
                from_table_id,
                from_column_id,
                to_table_id,
                to_column_id,
            )

    # ========================================================================
    # PHYSICAL TABLE RESOLUTION
    # ========================================================================

    @staticmethod
    def _resolve_physical_table(
        mapping,
        repository_validator,
    ):

        source_type = mapping.get(
            "source_type"
        )

        server = mapping.get(
            "server"
        )

        database = mapping.get(
            "database"
        )

        schema = mapping.get(
            "schema"
        )

        source_table = mapping.get(
            "source_table"
        )

        if (
            source_type == "SQL"
            and server
            and database
            and schema
            and source_table
        ):

            physical = (
                repository_validator.find_table(
                    server,
                    database,
                    schema,
                    source_table,
                )
            )

            if physical:
                return physical

        return None

    # ========================================================================
    # PHYSICAL COLUMN RESOLUTION
    # ========================================================================

    @staticmethod
    def _resolve_physical_column(
        mapping,
        physical_column,
        repository_validator,
    ):

        physical_table = (
            MetadataRepositoryWriter
            ._resolve_physical_table(
                mapping,
                repository_validator,
            )
        )

        if not physical_table:
            return None

        server = mapping[
            "server"
        ]

        database = mapping[
            "database"
        ]

        schema = mapping[
            "schema"
        ]

        source_table = mapping[
            "source_table"
        ]

        if not repository_validator.find_column(
            server,
            database,
            schema,
            source_table,
            physical_column,
        ):
            return None

        column_id = (
            MetadataRepositoryWriter
            ._find_physical_column_id(
                repository_validator,
                physical_table[
                    "table_id"
                ],
                physical_column,
            )
        )

        if not column_id:
            return None

        return {
            "table_id":
                physical_table[
                    "table_id"
                ],

            "column_id":
                column_id,
        }

    @staticmethod
    def _find_physical_column_id(
        repository_validator,
        table_id,
        column_name,
    ):

        cursor = (
            repository_validator.cursor
        )

        cursor.execute(
            """
            SELECT
                ColumnID
            FROM dbo.MetadataColumn
            WHERE TableID = ?
              AND LOWER(ColumnName) = LOWER(?)
            """,
            table_id,
            column_name,
        )

        row = cursor.fetchone()

        if not row:
            return None

        return int(
            row[0]
        )

    # ========================================================================
    # LOOKUPS
    # ========================================================================

    @staticmethod
    def _find_table_id(
        table_ids,
        table_name,
    ):

        if not table_name:
            return None

        return table_ids.get(
            table_name.lower()
        )

    @staticmethod
    def _find_column_id(
        column_ids,
        table_name,
        column_name,
    ):

        if (
            not table_name
            or not column_name
        ):
            return None

        return column_ids.get(
            (
                table_name.lower(),
                column_name.lower(),
            )
        )


# ============================================================================
# PROCESS ONE SEMANTIC MODEL
# ============================================================================

def process_semantic_model(
    client,
    repository_writer,
    repository_validator,
    model,
    connection,
    workspace_id,
    workspace_name,
):

    semantic_model_name = (
        model.get(
            "displayName"
        )
    )

    fabric_model_id = (
        model.get(
            "id"
        )
    )

    if not fabric_model_id:

        raise RuntimeError(
            "Semantic model does not contain an ID."
        )

    print()
    print("=" * 80)
    print(
        f"WORKSPACE: {workspace_name}"
    )
    print(
        f"WORKSPACE ID: {workspace_id}"
    )
    print(
        f"PROCESSING SEMANTIC MODEL: "
        f"{semantic_model_name}"
    )
    print("=" * 80)

    # ========================================================================
    # DEFINITION
    # ========================================================================

    print(
        "Retrieving semantic model definition..."
    )

    definition = (
        client.get_semantic_model_definition(
            workspace_id,
            fabric_model_id,
        )
    )

    extractor = SemanticModelExtractor(
        definition,
        workspace_id=workspace_id,
        semantic_model_id=fabric_model_id,
        semantic_model_name=semantic_model_name,
    )

    # ========================================================================
    # EXTRACTION
    # ========================================================================

    tables = (
        extractor.extract_tables()
    )

    columns = (
        extractor.extract_columns(
            tables
        )
    )

    measures = (
        extractor.extract_measures(
            tables
        )
    )

    relationships = (
        extractor.extract_relationships()
    )

    source_mappings = (
        extractor.extract_source_mappings(
            tables
        )
    )

    calculated_tables = (
        extractor.extract_calculated_tables(
            tables
        )
    )

    print()
    print(
        f"Tables discovered:        {len(tables)}"
    )

    print(
        f"Columns discovered:       {len(columns)}"
    )

    print(
        f"Measures discovered:      {len(measures)}"
    )

    print(
        f"Relationships discovered: {len(relationships)}"
    )

    print(
        f"Source mappings:           {len(source_mappings)}"
    )

    print(
        f"Calculated tables:        {len(calculated_tables)}"
    )

    # ========================================================================
    # PHYSICAL SOURCE LOADING
    # ========================================================================

    for mapping in source_mappings:

        if (
            mapping.get(
                "server"
            )
            and mapping.get(
                "database"
            )
        ):

            repository_validator.load_source_metadata(
                database_name=mapping[
                    "database"
                ],
                server_name=mapping[
                    "server"
                ],
            )

    # ========================================================================
    # DATABASE TRANSACTION
    # ========================================================================

    try:

        # --------------------------------------------------------------------
        # Workspace
        #
        # The workspace is upserted before the semantic model.
        # --------------------------------------------------------------------

        repository_writer.upsert_workspace(
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            is_enabled=True,
        )

        # --------------------------------------------------------------------
        # Semantic model
        # --------------------------------------------------------------------

        semantic_model_id = (
            repository_writer.get_or_create_model(
                {
                    "semantic_model_name":
                        semantic_model_name,

                    "workspace_id":
                        workspace_id,

                    "workspace_name":
                        workspace_name,

                    "fabric_model_id":
                        fabric_model_id,
                }
            )
        )

        print(
            f"Repository SemanticModelID: "
            f"{semantic_model_id}"
        )

        # --------------------------------------------------------------------
        # Rebuild children
        # --------------------------------------------------------------------

        repository_writer.clear_model_metadata(
            semantic_model_id
        )

        # --------------------------------------------------------------------
        # Tables
        # --------------------------------------------------------------------

        table_ids = (
            repository_writer.insert_tables(
                semantic_model_id,
                tables,
            )
        )

        # --------------------------------------------------------------------
        # Columns
        # --------------------------------------------------------------------

        column_ids = (
            repository_writer.insert_columns(
                table_ids,
                columns,
            )
        )

        # --------------------------------------------------------------------
        # Table sources
        # --------------------------------------------------------------------

        repository_writer.insert_table_sources(
            table_ids,
            tables,
            source_mappings,
            repository_validator,
        )

        # --------------------------------------------------------------------
        # Column sources
        # --------------------------------------------------------------------

        repository_writer.insert_column_sources(
            column_ids,
            columns,
            source_mappings,
            repository_validator,
        )

        # --------------------------------------------------------------------
        # Calculated column dependencies
        # --------------------------------------------------------------------

        repository_writer.insert_column_dependencies(
            columns,
            column_ids,
        )

        # --------------------------------------------------------------------
        # Measures
        # --------------------------------------------------------------------

        measure_ids = (
            repository_writer.insert_measures(
                semantic_model_id,
                table_ids,
                measures,
            )
        )

        # --------------------------------------------------------------------
        # Measure dependencies
        # --------------------------------------------------------------------

        repository_writer.insert_measure_dependencies(
            measures,
            measure_ids,
            table_ids,
            column_ids,
        )

        # --------------------------------------------------------------------
        # Calculated table dependencies
        # --------------------------------------------------------------------

        repository_writer.insert_table_dependencies(
            calculated_tables,
            table_ids,
            column_ids,
        )

        # --------------------------------------------------------------------
        # Relationships
        # --------------------------------------------------------------------

        repository_writer.insert_relationships(
            semantic_model_id,
            table_ids,
            column_ids,
            relationships,
        )

        # --------------------------------------------------------------------
        # Commit
        # --------------------------------------------------------------------

        connection.commit()

        print()
        print(
            "Repository update committed successfully."
        )

    except Exception:

        connection.rollback()

        logging.exception(
            "Repository transaction rolled back."
        )

        raise

    return {
        "workspace_id":
            workspace_id,

        "workspace_name":
            workspace_name,

        "semantic_model_name":
            semantic_model_name,

        "fabric_model_id":
            fabric_model_id,

        "semantic_model_id":
            semantic_model_id,

        "tables":
            tables,

        "columns":
            columns,

        "measures":
            measures,

        "relationships":
            relationships,

        "source_mappings":
            source_mappings,

        "calculated_tables":
            calculated_tables,
    }


# ============================================================================
# PRINT SUMMARY
# ============================================================================

def print_model_summary(
    result,
):

    print()
    print("-" * 80)

    print(
        f"WORKSPACE: "
        f"{result['workspace_name']}"
    )

    print(
        f"Workspace ID: "
        f"{result['workspace_id']}"
    )

    print(
        f"MODEL: "
        f"{result['semantic_model_name']}"
    )

    print(
        f"Repository ID: "
        f"{result['semantic_model_id']}"
    )

    print(
        f"Fabric ID: "
        f"{result['fabric_model_id']}"
    )

    print(
        f"Tables: "
        f"{len(result['tables'])}"
    )

    print(
        f"Columns: "
        f"{len(result['columns'])}"
    )

    print(
        f"Measures: "
        f"{len(result['measures'])}"
    )

    print(
        f"Relationships: "
        f"{len(result['relationships'])}"
    )

    print(
        f"Source mappings: "
        f"{len(result['source_mappings'])}"
    )

    print(
        f"Calculated tables: "
        f"{len(result['calculated_tables'])}"
    )


# ============================================================================
# MAIN
# ============================================================================

def main():

    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
    )

    print("=" * 80)
    print(
        "FABRIC SEMANTIC MODEL METADATA EXTRACTOR V6.3"
    )
    print("=" * 80)

    # ========================================================================
    # LOAD WORKSPACES
    # ========================================================================

    print()
    print(
        "Loading workspace configuration..."
    )

    workspaces = (
        load_enabled_workspaces()
    )

    print()
    print(
        f"Enabled workspaces: "
        f"{len(workspaces)}"
    )

    for index, workspace in enumerate(
        workspaces,
        start=1,
    ):

        print(
            f"{index}. "
            f"{workspace['workspace_name']}"
            f" | "
            f"{workspace['workspace_id']}"
        )

    print()

    print(
        "IMPORTANT:"
    )

    print(
        "Only the workspaces listed above with "
        "enabled=true will be processed."
    )

    print()

    print(
        "Workspace metadata will automatically be "
        "inserted/updated in dbo.MetadataWorkspace."
    )

    print()

    print(
        "Architecture:"
    )

    print(
        "Configured Fabric Workspaces"
    )

    print(
        "        ↓"
    )

    print(
        "dbo.MetadataWorkspace"
    )

    print(
        "        ↓"
    )

    print(
        "Fabric REST API / TMDL"
    )

    print(
        "        ↓"
    )

    print(
        "SemanticModelExtractor V6.3"
    )

    print(
        "        ↓"
    )

    print(
        "MetadataRepositoryWriter"
    )

    print(
        "        ↓"
    )

    print(
        "MetadataRepository Warehouse"
    )

    print()

    connection = None

    try:

        # ====================================================================
        # FABRIC CLIENT
        # ====================================================================

        print(
            "Creating Fabric client..."
        )

        client = FabricClient()

        # ====================================================================
        # REPOSITORY CONNECTION
        # ====================================================================

        print()
        print("=" * 80)
        print(
            "CONNECTING TO METADATA REPOSITORY"
        )
        print("=" * 80)

        print()
        print(
            "A Microsoft Entra login window should appear..."
        )

        connection = (
            connect_to_fabric_warehouse(
                DEFAULT_DRIVER,
                FABRIC_SQL_SERVER,
                FABRIC_SQL_DATABASE,
            )
        )

        cursor = connection.cursor()

        print(
            "Connected successfully."
        )

        # ====================================================================
        # COMPONENTS
        # ====================================================================

        repository_validator = (
            RepositoryValidator(
                cursor
            )
        )

        repository_writer = (
            MetadataRepositoryWriter(
                cursor
            )
        )

        # ====================================================================
        # GLOBAL RESULTS
        # ====================================================================

        all_results = []

        total_models_discovered = 0

        total_successful = 0

        total_failed = 0

        total_workspaces_successful = 0

        total_workspaces_failed = 0

        # ====================================================================
        # PROCESS EACH ENABLED WORKSPACE
        # ====================================================================

        for workspace in workspaces:

            workspace_id = workspace[
                "workspace_id"
            ]

            workspace_name = workspace[
                "workspace_name"
            ]

            print()
            print()
            print("#" * 80)
            print(
                f"WORKSPACE: {workspace_name}"
            )
            print(
                f"WORKSPACE ID: {workspace_id}"
            )
            print("#" * 80)

            # ================================================================
            # REGISTER WORKSPACE IN REPOSITORY
            # ================================================================

            try:

                repository_writer.upsert_workspace(
                    workspace_id=workspace_id,
                    workspace_name=workspace_name,
                    is_enabled=True,
                )

                connection.commit()

                print(
                    "Workspace metadata synchronized."
                )

            except Exception:

                connection.rollback()

                logging.exception(
                    "Failed to synchronize workspace metadata: "
                    "%s",
                    workspace_name,
                )

                total_workspaces_failed += 1

                continue

            # ================================================================
            # DISCOVER SEMANTIC MODELS
            # ================================================================

            print()
            print(
                "Discovering semantic models..."
            )

            try:

                semantic_models = (
                    client.get_items_by_type(
                        workspace_id,
                        "SemanticModel",
                    )
                )

            except Exception:

                logging.exception(
                    "Failed to discover semantic models "
                    "in workspace: %s",
                    workspace_name,
                )

                print()
                print(
                    f"ERROR discovering workspace: "
                    f"{workspace_name}"
                )

                total_workspaces_failed += 1

                continue

            total_workspaces_successful += 1

            print(
                f"Semantic models discovered: "
                f"{len(semantic_models)}"
            )

            total_models_discovered += (
                len(semantic_models)
            )

            if not semantic_models:

                print(
                    "No semantic models found in this workspace."
                )

                continue

            for index, model in enumerate(
                semantic_models,
                start=1,
            ):

                print(
                    f"{index}. "
                    f"{model.get('displayName')}"
                    f" | "
                    f"{model.get('id')}"
                )

            # ================================================================
            # PROCESS MODELS
            # ================================================================

            for model in semantic_models:

                try:

                    result = (
                        process_semantic_model(
                            client,
                            repository_writer,
                            repository_validator,
                            model,
                            connection,
                            workspace_id,
                            workspace_name,
                        )
                    )

                    all_results.append(
                        result
                    )

                    total_successful += 1

                except Exception as exc:

                    total_failed += 1

                    logging.exception(
                        "Failed semantic model: %s "
                        "in workspace: %s",
                        model.get(
                            "displayName"
                        ),
                        workspace_name,
                    )

                    print()
                    print(
                        f"ERROR: "
                        f"{model.get('displayName')}"
                    )

                    print(
                        f"Workspace: "
                        f"{workspace_name}"
                    )

                    print(
                        str(exc)
                    )

        # ====================================================================
        # FINAL SUMMARY
        # ====================================================================

        print()
        print()
        print("=" * 80)
        print(
            "V6.3 EXTRACTION SUMMARY"
        )
        print("=" * 80)

        print()

        print(
            f"Configured workspaces:       "
            f"{len(workspaces)}"
        )

        print(
            f"Workspaces synchronized:     "
            f"{total_workspaces_successful}"
        )

        print(
            f"Workspace failures:          "
            f"{total_workspaces_failed}"
        )

        print(
            f"Semantic models discovered:  "
            f"{total_models_discovered}"
        )

        print(
            f"Successfully processed:      "
            f"{total_successful}"
        )

        print(
            f"Failed:                      "
            f"{total_failed}"
        )

        # ====================================================================
        # WORKSPACE SUMMARY
        # ====================================================================

        print()
        print(
            "WORKSPACES PROCESSED"
        )

        print(
            "-" * 80
        )

        for workspace in workspaces:

            workspace_results = [
                result
                for result in all_results
                if result[
                    "workspace_id"
                ]
                == workspace[
                    "workspace_id"
                ]
            ]

            print()
            print(
                f"Workspace: "
                f"{workspace['workspace_name']}"
            )

            print(
                f"Workspace ID: "
                f"{workspace['workspace_id']}"
            )

            print(
                f"Models successfully processed: "
                f"{len(workspace_results)}"
            )

        # ====================================================================
        # MODEL DETAILS
        # ====================================================================

        for result in all_results:

            print_model_summary(
                result
            )

        print()
        print("=" * 80)

        if (
            total_failed == 0
            and total_workspaces_failed == 0
        ):

            print(
                "V6.3 EXTRACTION COMPLETED SUCCESSFULLY"
            )

        else:

            print(
                "V6.3 EXTRACTION COMPLETED WITH ERRORS"
            )

        print("=" * 80)

    except Exception:

        logging.exception(
            "Semantic model extraction failed."
        )

        raise

    finally:

        if connection:

            connection.close()

            logging.info(
                "MetadataRepository connection closed."
            )


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":

    main()