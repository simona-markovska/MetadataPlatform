import base64
import json
import logging
import re
import sys
from pathlib import Path

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
# LOGGING
# ============================================================================

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


# ============================================================================
# CONFIGURATION
# ============================================================================

WORKSPACE_ID = "7a6e5bfa-8068-4e86-89f5-d6f629ab7ced"

WORKSPACE_NAME = "Metadata Intelligence Platform"


# ============================================================================
# FABRIC WAREHOUSE
# ============================================================================

FABRIC_SQL_SERVER = (
    "j7mjaqg22d2ujb27llpciiyism-7jnw46tiqcde5cpv233ctk345u"
    ".datawarehouse.fabric.microsoft.com"
)

FABRIC_SQL_DATABASE = "MetadataRepository"


# ============================================================================
# OUTPUT
# ============================================================================

OUTPUT_DIR = ROOT_DIR / "output"

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================================
# UUID HELPERS
# ============================================================================

UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)


def is_uuid(value):
    """Return True when value looks like a GUID."""

    if value is None:
        return False

    return bool(
        UUID_PATTERN.match(
            str(value)
        )
    )


# ============================================================================
# FABRIC WAREHOUSE CONNECTION
# ============================================================================


def get_fabric_connection_string(
    driver,
    server,
    database,
):
    """Build the Fabric Warehouse ODBC connection string."""

    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Authentication=ActiveDirectoryInteractive;"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
    )


def connect_to_fabric_warehouse(
    driver,
    server,
    database,
):
    """Open an interactive Microsoft Entra connection."""

    connection_string = get_fabric_connection_string(
        driver,
        server,
        database,
    )

    logging.info(
        "Opening Microsoft Entra interactive authentication..."
    )

    return pyodbc.connect(
        connection_string
    )


# ============================================================================
# REPORT DISCOVERY
# ============================================================================


def normalize_workspace_items(
    response,
):
    """
    Normalize Fabric workspace-items response.

    Supports:
        {"value": [...]}
        {"items": [...]}
        [...]
    """

    if isinstance(
        response,
        dict,
    ):

        value = response.get(
            "value"
        )

        if isinstance(
            value,
            list,
        ):

            return value

        items = response.get(
            "items"
        )

        if isinstance(
            items,
            list,
        ):

            return items

        raise RuntimeError(
            "Unexpected workspace-items response format. "
            f"Dictionary keys: {list(response.keys())}"
        )

    if isinstance(
        response,
        list,
    ):

        return response

    raise RuntimeError(
        "Unexpected workspace-items response type: "
        f"{type(response).__name__}"
    )


def get_workspace_items(
    client,
    workspace_id,
):
    """Retrieve and normalize all workspace items."""

    response = client.get_workspace_items(
        workspace_id
    )

    items = normalize_workspace_items(
        response
    )

    return [
        item
        for item in items
        if isinstance(
            item,
            dict,
        )
    ]


def discover_reports(
    client,
    workspace_id,
):
    """Discover all Fabric reports in the workspace."""

    logging.info(
        "Discovering reports automatically..."
    )

    items = get_workspace_items(
        client,
        workspace_id,
    )

    reports = []

    for item in items:

        item_type = (
            item.get("type")
            or item.get("itemType")
        )

        if str(item_type).lower() != "report":
            continue

        report_id = (
            item.get("id")
            or item.get("itemId")
            or item.get("reportId")
        )

        report_name = (
            item.get("displayName")
            or item.get("name")
            or item.get("reportName")
        )

        if not report_id:

            logging.warning(
                "Skipping report without ID: %s",
                item,
            )

            continue

        if not report_name:
            report_name = str(report_id)

        reports.append(
            {
                "id": str(report_id),
                "name": str(report_name),
                "type": "Report",
                "raw": item,
            }
        )

    reports.sort(
        key=lambda x: x["name"].lower()
    )

    logging.info(
        "Reports discovered: %d",
        len(reports),
    )

    return reports


# ============================================================================
# SEMANTIC MODEL DISCOVERY
# ============================================================================


def collect_semantic_model_candidates(
    value,
    candidates=None,
    parent_key="",
):
    """
    Recursively search an object for values stored under keys that
    look related to semantic models, datasets, or models.
    """

    if candidates is None:
        candidates = []

    if isinstance(
        value,
        dict,
    ):

        for key, child in value.items():

            key_text = str(
                key
            ).lower()

            semantic_key = (
                "semantic" in key_text
                and "model" in key_text
            )

            dataset_key = (
                "dataset" in key_text
                and (
                    "id" in key_text
                    or "model" in key_text
                )
            )

            model_key = (
                key_text in {
                    "modelid",
                    "semanticmodelid",
                    "datasetid",
                    "dataset_id",
                }
            )

            if (
                semantic_key
                or dataset_key
                or model_key
            ):

                if isinstance(
                    child,
                    str,
                ) and is_uuid(child):

                    candidates.append(
                        child
                    )

                elif isinstance(
                    child,
                    dict,
                ):

                    for candidate_key in (
                        "id",
                        "modelId",
                        "modelID",
                        "semanticModelId",
                        "semanticModelID",
                        "datasetId",
                        "datasetID",
                    ):

                        candidate = child.get(
                            candidate_key
                        )

                        if is_uuid(
                            candidate
                        ):

                            candidates.append(
                                str(candidate)
                            )

            collect_semantic_model_candidates(
                child,
                candidates,
                key_text,
            )

    elif isinstance(
        value,
        list,
    ):

        for child in value:

            collect_semantic_model_candidates(
                child,
                candidates,
                parent_key,
            )

    return candidates


def extract_report_semantic_model_id(
    definition,
):
    """
    Try to identify the semantic model Fabric ID from the
    report definition.
    """

    candidates = []

    for part in get_definition_parts(
        definition
    ):

        path = part.get(
            "path",
            "",
        )

        # report.json is the most relevant location
        if path == "definition/report.json":

            data = parse_json_part(
                part
            )

            if data:

                candidates.extend(
                    collect_semantic_model_candidates(
                        data
                    )
                )

    # Remove duplicates while preserving order
    unique_candidates = []

    for candidate in candidates:

        if candidate not in unique_candidates:

            unique_candidates.append(
                candidate
            )

    if unique_candidates:

        logging.info(
            "Semantic model candidate found in report definition: %s",
            unique_candidates[0],
        )

        return unique_candidates[0]

    return None


def extract_semantic_model_id_from_workspace_item(
    report_item,
):
    """
    Try to identify a semantic model ID directly from the
    workspace item representing the report.
    """

    if not isinstance(
        report_item,
        dict,
    ):

        return None

    candidates = collect_semantic_model_candidates(
        report_item
    )

    for candidate in candidates:

        if is_uuid(candidate):

            return str(candidate)

    return None


def discover_semantic_models(
    workspace_items,
):
    """Return all SemanticModel items in the workspace."""

    semantic_models = []

    for item in workspace_items:

        item_type = (
            item.get("type")
            or item.get("itemType")
        )

        if str(item_type).lower() != "semanticmodel":
            continue

        model_id = (
            item.get("id")
            or item.get("itemId")
            or item.get("semanticModelId")
        )

        model_name = (
            item.get("displayName")
            or item.get("name")
            or item.get("modelName")
        )

        if not model_id:
            continue

        semantic_models.append(
            {
                "id": str(model_id),
                "name": (
                    str(model_name)
                    if model_name
                    else str(model_id)
                ),
                "raw": item,
            }
        )

    return semantic_models


def resolve_semantic_model_for_report(
    client,
    workspace_id,
    report,
    definition,
    cursor,
):
    """
    Resolve the Fabric semantic model connected to a report.

    Resolution order:

    1. Report definition
    2. Report workspace item metadata
    3. Existing repository semantic model matching the
       discovered Fabric semantic model
    4. If the workspace contains exactly one semantic model,
       use it as a safe fallback

    This function deliberately does NOT depend on MetadataReport
    already existing.
    """

    # ------------------------------------------------------------------------
    # 1. Report definition
    # ------------------------------------------------------------------------

    fabric_model_id = (
        extract_report_semantic_model_id(
            definition
        )
    )

    if fabric_model_id:

        logging.info(
            "Semantic model discovered from report definition: %s",
            fabric_model_id,
        )

        return fabric_model_id

    logging.warning(
        "Semantic model was not identified directly "
        "from the report definition."
    )

    # ------------------------------------------------------------------------
    # 2. Workspace item metadata
    # ------------------------------------------------------------------------

    workspace_items = get_workspace_items(
        client,
        workspace_id,
    )

    report_item = None

    for item in workspace_items:

        item_id = (
            item.get("id")
            or item.get("itemId")
            or item.get("reportId")
        )

        if str(item_id) == str(
            report["id"]
        ):

            report_item = item

            break

    if report_item:

        fabric_model_id = (
            extract_semantic_model_id_from_workspace_item(
                report_item
            )
        )

        if fabric_model_id:

            logging.info(
                "Semantic model discovered from "
                "report workspace metadata: %s",
                fabric_model_id,
            )

            return fabric_model_id

    # ------------------------------------------------------------------------
    # 3. Discover semantic models in workspace
    # ------------------------------------------------------------------------

    semantic_models = discover_semantic_models(
        workspace_items
    )

    logging.info(
        "Semantic models discovered in workspace: %d",
        len(semantic_models),
    )

    # ------------------------------------------------------------------------
    # 4. Match repository model names where possible
    # ------------------------------------------------------------------------

    if semantic_models:

        for model in semantic_models:

            cursor.execute(
                """
                SELECT
                    SemanticModelID,
                    ModelName,
                    FabricModelID
                FROM dbo.MetadataSemanticModel
                WHERE FabricModelID = ?
                """,
                model["id"],
            )

            result = cursor.fetchone()

            if result:

                logging.info(
                    "Semantic model matched to repository: "
                    "%s (%s)",
                    model["name"],
                    model["id"],
                )

                return str(
                    model["id"]
                )

    # ------------------------------------------------------------------------
    # 5. Safe single-model fallback
    # ------------------------------------------------------------------------

    if len(
        semantic_models
    ) == 1:

        model = semantic_models[0]

        logging.warning(
            "Using the only semantic model in the workspace "
            "as the report's semantic model: %s (%s)",
            model["name"],
            model["id"],
        )

        return model["id"]

    # ------------------------------------------------------------------------
    # 6. Could not resolve
    # ------------------------------------------------------------------------

    available_models = ", ".join(
        f"{model['name']} [{model['id']}]"
        for model in semantic_models
    )

    raise RuntimeError(
        "Could not determine the semantic model for report "
        f"'{report['name']}'. "
        f"Semantic models available in workspace: "
        f"{available_models or 'none'}"
    )


# ============================================================================
# REPORT DEFINITION HELPERS
# ============================================================================


def decode_definition_part(
    part,
):
    """Decode an InlineBase64 Fabric definition part."""

    payload = part.get(
        "payload"
    )

    if not payload:
        return ""

    payload_type = part.get(
        "payloadType"
    )

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
            "Failed to decode report definition part."
        )

        return ""


def get_definition_parts(
    definition,
):
    """Return report-definition parts."""

    if not isinstance(
        definition,
        dict,
    ):

        raise RuntimeError(
            "Report definition is not a dictionary."
        )

    definition_object = definition.get(
        "definition",
        {},
    )

    if isinstance(
        definition_object,
        dict,
    ):

        parts = definition_object.get(
            "parts",
            [],
        )

        if isinstance(
            parts,
            list,
        ):

            return parts

    return []


def save_report_definition(
    report_name,
    definition,
):
    """Save the raw report definition for troubleshooting."""

    safe_name = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        report_name,
    )

    output_file = (
        OUTPUT_DIR
        / f"report_definition_{safe_name}.json"
    )

    try:

        with open(
            output_file,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                definition,
                file,
                indent=2,
                ensure_ascii=False,
            )

        logging.info(
            "Saved report definition: %s",
            output_file,
        )

    except Exception:

        logging.exception(
            "Could not save report definition."
        )


def parse_json_part(
    part,
):
    """Decode a definition part and parse it as JSON."""

    content = decode_definition_part(
        part
    )

    if not content:
        return None

    try:

        return json.loads(
            content
        )

    except json.JSONDecodeError:

        logging.debug(
            "Definition part is not JSON: %s",
            part.get("path"),
        )

        return None


# ============================================================================
# REPORT METADATA EXTRACTION
# ============================================================================


class ReportMetadataExtractor:
    """
    Extract meaningful report metadata.

    Deliberately excludes visual layout information such as:

        X
        Y
        Width
        Height
        ZOrder
        TabOrder

    The useful lineage information is:

        Report
          -> Page
          -> Visual
          -> VisualField
          -> SemanticTable / SemanticColumn / Measure
    """

    def __init__(
        self,
        definition,
    ):

        self.definition = definition

        self.parts = get_definition_parts(
            definition
        )

    # ========================================================================
    # PAGES
    # ========================================================================

    def extract_pages(
        self,
    ):

        pages = []

        page_order = []

        # --------------------------------------------------------------------
        # pages.json
        # --------------------------------------------------------------------

        for part in self.parts:

            path = part.get(
                "path",
                "",
            )

            if path != "definition/pages/pages.json":
                continue

            data = parse_json_part(
                part
            )

            if not data:
                continue

            page_order = data.get(
                "pageOrder",
                [],
            )

            if not isinstance(
                page_order,
                list,
            ):

                page_order = []

        # --------------------------------------------------------------------
        # Individual page files
        # --------------------------------------------------------------------

        page_data = {}

        for part in self.parts:

            path = part.get(
                "path",
                "",
            )

            if not re.match(
                r"^definition/pages/[^/]+/page\.json$",
                path,
            ):

                continue

            data = parse_json_part(
                part
            )

            if not data:
                continue

            page_id = data.get(
                "name"
            )

            if not page_id:

                match = re.match(
                    r"^definition/pages/([^/]+)/page\.json$",
                    path,
                )

                if match:
                    page_id = match.group(1)

            if not page_id:
                continue

            page_data[
                page_id
            ] = (
                data,
                path,
            )

        # --------------------------------------------------------------------
        # Preserve Fabric page order
        # --------------------------------------------------------------------

        ordered_ids = []

        for page_id in page_order:

            if page_id in page_data:

                ordered_ids.append(
                    page_id
                )

        for page_id in page_data:

            if page_id not in ordered_ids:

                ordered_ids.append(
                    page_id
                )

        # --------------------------------------------------------------------
        # Build result
        # --------------------------------------------------------------------

        for index, page_id in enumerate(
            ordered_ids,
            start=1,
        ):

            data, path = page_data[
                page_id
            ]

            pages.append(
                {
                    "page_name": page_id,

                    "display_name": data.get(
                        "displayName"
                    ),

                    "page_order": index,
                }
            )

        return pages

    # ========================================================================
    # VISUALS
    # ========================================================================

    def extract_visuals(
        self,
    ):

        visuals = []

        for part in self.parts:

            path = part.get(
                "path",
                "",
            )

            match = re.match(
                r"^definition/pages/([^/]+)/visuals/([^/]+)/visual\.json$",
                path,
            )

            if not match:
                continue

            page_name = match.group(
                1
            )

            visual_id_from_path = match.group(
                2
            )

            data = parse_json_part(
                part
            )

            if not data:
                continue

            fabric_visual_id = (
                data.get("name")
                or visual_id_from_path
            )

            visual_definition = data.get(
                "visual",
                {},
            )

            visual_type = (
                visual_definition.get(
                    "visualType"
                )
            )

            if not visual_type:

                visual_type = (
                    data.get(
                        "visualType"
                    )
                    or "Unknown"
                )

            visuals.append(
                {
                    "page_name": page_name,

                    "fabric_visual_id":
                        fabric_visual_id,

                    "visual_type":
                        visual_type,

                    "definition_path": path,

                    "raw": data,
                }
            )

        return visuals

    # ========================================================================
    # VISUAL FIELDS
    # ========================================================================

    def extract_visual_fields(
        self,
        visual,
    ):
        """
        Extract meaningful fields used by a visual.

        Captures:

            Column
            Measure
            Aggregation

        and:

            ProjectionArea
            QueryRef
            NativeQueryRef
        """

        result = []

        raw = visual.get(
            "raw",
            {},
        )

        visual_definition = raw.get(
            "visual",
            {},
        )

        query = visual_definition.get(
            "query",
            {},
        )

        query_state = query.get(
            "queryState",
            {},
        )

        if not isinstance(
            query_state,
            dict,
        ):

            return result

        for projection_area, state in query_state.items():

            if not isinstance(
                state,
                dict,
            ):

                continue

            projections = state.get(
                "projections",
                [],
            )

            if not isinstance(
                projections,
                list,
            ):

                continue

            for projection in projections:

                if not isinstance(
                    projection,
                    dict,
                ):

                    continue

                field = projection.get(
                    "field",
                    {},
                )

                query_ref = projection.get(
                    "queryRef"
                )

                native_query_ref = projection.get(
                    "nativeQueryRef"
                )

                field_metadata = self._parse_field(
                    field
                )

                if not field_metadata:
                    continue

                field_metadata[
                    "projection_area"
                ] = projection_area

                field_metadata[
                    "query_ref"
                ] = query_ref

                field_metadata[
                    "native_query_ref"
                ] = native_query_ref

                result.append(
                    field_metadata
                )

        return result

    # ========================================================================
    # FIELD PARSER
    # ========================================================================

    def _parse_field(
        self,
        field,
    ):
        """Normalize a report field."""

        if not isinstance(
            field,
            dict,
        ):

            return None

        # --------------------------------------------------------------------
        # Measure
        # --------------------------------------------------------------------

        if "Measure" in field:

            measure = field.get(
                "Measure",
                {},
            )

            expression = measure.get(
                "Expression",
                {},
            )

            source_ref = expression.get(
                "SourceRef",
                {},
            )

            table_name = source_ref.get(
                "Entity"
            )

            measure_name = measure.get(
                "Property"
            )

            if not measure_name:
                return None

            return {
                "field_type": "Measure",

                "table_name": table_name,

                "column_name": None,

                "measure_name": measure_name,

                "aggregation_function": None,
            }

        # --------------------------------------------------------------------
        # Column
        # --------------------------------------------------------------------

        if "Column" in field:

            column = field.get(
                "Column",
                {},
            )

            expression = column.get(
                "Expression",
                {},
            )

            source_ref = expression.get(
                "SourceRef",
                {},
            )

            table_name = source_ref.get(
                "Entity"
            )

            column_name = column.get(
                "Property"
            )

            if not column_name:
                return None

            return {
                "field_type": "Column",

                "table_name": table_name,

                "column_name": column_name,

                "measure_name": None,

                "aggregation_function": None,
            }

        # --------------------------------------------------------------------
        # Aggregation
        # --------------------------------------------------------------------

        if "Aggregation" in field:

            aggregation = field.get(
                "Aggregation",
                {},
            )

            expression = aggregation.get(
                "Expression",
                {},
            )

            function_code = aggregation.get(
                "Function"
            )

            column = expression.get(
                "Column",
                {},
            )

            column_expression = column.get(
                "Expression",
                {},
            )

            source_ref = column_expression.get(
                "SourceRef",
                {},
            )

            table_name = source_ref.get(
                "Entity"
            )

            column_name = column.get(
                "Property"
            )

            if not column_name:
                return None

            return {
                "field_type": "Column",

                "table_name": table_name,

                "column_name": column_name,

                "measure_name": None,

                "aggregation_function":
                    self._aggregation_name(
                        function_code
                    ),
            }

        return None

    # ========================================================================
    # AGGREGATION
    # ========================================================================

    @staticmethod
    def _aggregation_name(
        function_code,
    ):

        mapping = {
            0: "SUM",
            1: "AVERAGE",
            2: "MIN",
            3: "MAX",
            4: "COUNT",
            5: "COUNTROWS",
            6: "DISTINCTCOUNT",
            7: "NONE",
        }

        return mapping.get(
            function_code,
            (
                str(function_code)
                if function_code is not None
                else None
            ),
        )

    # ========================================================================
    # FILTERS
    # ========================================================================

    def extract_visual_filters(
        self,
        visual,
    ):

        result = []

        raw = visual.get(
            "raw",
            {},
        )

        filter_config = raw.get(
            "filterConfig",
            {},
        )

        filters = filter_config.get(
            "filters",
            [],
        )

        if not isinstance(
            filters,
            list,
        ):

            return result

        for filter_definition in filters:

            if not isinstance(
                filter_definition,
                dict,
            ):

                continue

            filter_name = filter_definition.get(
                "name"
            )

            filter_type = filter_definition.get(
                "type"
            )

            field = filter_definition.get(
                "field",
                {},
            )

            field_metadata = self._parse_field(
                field
            )

            if field_metadata:

                result.append(
                    {
                        "filter_name":
                            filter_name,

                        "field_type":
                            field_metadata[
                                "field_type"
                            ],

                        "table_name":
                            field_metadata[
                                "table_name"
                            ],

                        "column_name":
                            field_metadata[
                                "column_name"
                            ],

                        "measure_name":
                            field_metadata[
                                "measure_name"
                            ],

                        "filter_type":
                            filter_type,
                    }
                )

            else:

                result.append(
                    {
                        "filter_name":
                            filter_name,

                        "field_type":
                            None,

                        "table_name":
                            None,

                        "column_name":
                            None,

                        "measure_name":
                            None,

                        "filter_type":
                            filter_type,
                    }
                )

        return result


# ============================================================================
# SEMANTIC MODEL LOOKUPS
# ============================================================================


def build_semantic_table_lookup(
    cursor,
):

    cursor.execute(
        """
        SELECT
            SemanticTableID,
            SemanticModelID,
            TableName
        FROM dbo.MetadataSemanticTable
        """
    )

    lookup = {}

    for (
        semantic_table_id,
        semantic_model_id,
        table_name,
    ) in cursor.fetchall():

        lookup[
            (
                int(semantic_model_id),
                str(table_name),
            )
        ] = int(
            semantic_table_id
        )

    return lookup


def build_semantic_column_lookup(
    cursor,
):

    cursor.execute(
        """
        SELECT
            sc.SemanticColumnID,
            st.SemanticTableID,
            st.SemanticModelID,
            st.TableName,
            sc.ColumnName
        FROM dbo.MetadataSemanticColumn sc
        INNER JOIN dbo.MetadataSemanticTable st
            ON sc.SemanticTableID = st.SemanticTableID
        """
    )

    lookup = {}

    for (
        semantic_column_id,
        semantic_table_id,
        semantic_model_id,
        table_name,
        column_name,
    ) in cursor.fetchall():

        lookup[
            (
                int(semantic_model_id),
                str(table_name),
                str(column_name),
            )
        ] = int(
            semantic_column_id
        )

    return lookup


def build_measure_lookup(
    cursor,
):

    cursor.execute(
        """
        SELECT
            MeasureID,
            SemanticModelID,
            MeasureName
        FROM dbo.MetadataMeasure
        """
    )

    lookup = {}

    for (
        measure_id,
        semantic_model_id,
        measure_name,
    ) in cursor.fetchall():

        lookup[
            (
                int(semantic_model_id),
                str(measure_name),
            )
        ] = int(
            measure_id
        )

    return lookup


# ============================================================================
# SEMANTIC MODEL REPOSITORY ID
# ============================================================================


def get_repository_semantic_model_id(
    cursor,
    fabric_model_id,
):
    """Resolve FabricModelID -> repository SemanticModelID."""

    cursor.execute(
        """
        SELECT
            SemanticModelID
        FROM dbo.MetadataSemanticModel
        WHERE FabricModelID = ?
        """,
        fabric_model_id,
    )

    result = cursor.fetchone()

    if not result:

        raise RuntimeError(
            "Fabric semantic model exists in Fabric but was not "
            "found in MetadataSemanticModel. "
            f"FabricModelID: {fabric_model_id}"
        )

    return int(
        result[0]
    )


# ============================================================================
# REPORT REPOSITORY
# ============================================================================


def get_or_create_report(
    cursor,
    report_name,
    report_id,
    semantic_model_id,
):
    """
    Insert or update MetadataReport.

    This function is now reached even when the report does not
    already exist.
    """

    cursor.execute(
        """
        SELECT
            ReportID
        FROM dbo.MetadataReport
        WHERE FabricReportID = ?
        """,
        report_id,
    )

    result = cursor.fetchone()

    if result:

        repository_report_id = int(
            result[0]
        )

        cursor.execute(
            """
            UPDATE dbo.MetadataReport
            SET
                ReportName = ?,
                WorkspaceID = ?,
                WorkspaceName = ?,
                SemanticModelID = ?,
                SourceType = ?
            WHERE ReportID = ?
            """,
            report_name,
            WORKSPACE_ID,
            WORKSPACE_NAME,
            semantic_model_id,
            "Microsoft Fabric Report",
            repository_report_id,
        )

        cursor.connection.commit()

        logging.info(
            "Updated existing MetadataReport: %s",
            repository_report_id,
        )

        return repository_report_id

    cursor.execute(
        """
        INSERT INTO dbo.MetadataReport
        (
            ReportName,
            WorkspaceID,
            WorkspaceName,
            FabricReportID,
            SemanticModelID,
            SourceType
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        report_name,
        WORKSPACE_ID,
        WORKSPACE_NAME,
        report_id,
        semantic_model_id,
        "Microsoft Fabric Report",
    )

    cursor.connection.commit()

    cursor.execute(
        """
        SELECT
            ReportID
        FROM dbo.MetadataReport
        WHERE FabricReportID = ?
        """,
        report_id,
    )

    result = cursor.fetchone()

    if not result:

        raise RuntimeError(
            "Could not retrieve ReportID after insert."
        )

    repository_report_id = int(
        result[0]
    )

    logging.info(
        "Created MetadataReport: %s",
        repository_report_id,
    )

    return repository_report_id


# ============================================================================
# CLEAR REPORT CHILD METADATA
# ============================================================================


def clear_report_children(
    cursor,
    report_id,
):
    """
    Refresh report child metadata.

    Delete order:

        VisualFilter
        VisualField
        Visual
        Page
    """

    cursor.execute(
        """
        DELETE FROM dbo.MetadataReportVisualFilter
        WHERE VisualID IN
        (
            SELECT VisualID
            FROM dbo.MetadataReportVisual
            WHERE PageID IN
            (
                SELECT PageID
                FROM dbo.MetadataReportPage
                WHERE ReportID = ?
            )
        )
        """,
        report_id,
    )

    cursor.execute(
        """
        DELETE FROM dbo.MetadataReportVisualField
        WHERE VisualID IN
        (
            SELECT VisualID
            FROM dbo.MetadataReportVisual
            WHERE PageID IN
            (
                SELECT PageID
                FROM dbo.MetadataReportPage
                WHERE ReportID = ?
            )
        )
        """,
        report_id,
    )

    cursor.execute(
        """
        DELETE FROM dbo.MetadataReportVisual
        WHERE PageID IN
        (
            SELECT PageID
            FROM dbo.MetadataReportPage
            WHERE ReportID = ?
        )
        """,
        report_id,
    )

    cursor.execute(
        """
        DELETE FROM dbo.MetadataReportPage
        WHERE ReportID = ?
        """,
        report_id,
    )

    cursor.connection.commit()

    logging.info(
        "Cleared existing child metadata for ReportID %s.",
        report_id,
    )


# ============================================================================
# LOAD PAGES
# ============================================================================


def load_pages(
    cursor,
    report_id,
    pages,
):

    page_lookup = {}

    for page in pages:

        cursor.execute(
            """
            INSERT INTO dbo.MetadataReportPage
            (
                ReportID,
                PageName,
                DisplayName,
                PageOrder
            )
            VALUES (?, ?, ?, ?)
            """,
            report_id,
            page["page_name"],
            page["display_name"],
            page["page_order"],
        )

        cursor.execute(
            """
            SELECT
                PageID
            FROM dbo.MetadataReportPage
            WHERE ReportID = ?
              AND PageName = ?
            """,
            report_id,
            page["page_name"],
        )

        result = cursor.fetchone()

        if not result:

            raise RuntimeError(
                "Could not retrieve PageID for page: "
                f"{page['page_name']}"
            )

        page_lookup[
            page["page_name"]
        ] = int(
            result[0]
        )

    cursor.connection.commit()

    logging.info(
        "Pages loaded: %d",
        len(pages),
    )

    return page_lookup


# ============================================================================
# LOAD VISUALS
# ============================================================================


def load_visuals(
    cursor,
    page_lookup,
    visuals,
):

    visual_lookup = {}

    for visual in visuals:

        page_id = page_lookup.get(
            visual["page_name"]
        )

        if page_id is None:

            logging.warning(
                "Page not found for visual %s: %s",
                visual["fabric_visual_id"],
                visual["page_name"],
            )

            continue

        cursor.execute(
            """
            INSERT INTO dbo.MetadataReportVisual
            (
                PageID,
                FabricVisualID,
                VisualType
            )
            VALUES (?, ?, ?)
            """,
            page_id,
            visual["fabric_visual_id"],
            visual["visual_type"],
        )

        cursor.execute(
            """
            SELECT
                VisualID
            FROM dbo.MetadataReportVisual
            WHERE PageID = ?
              AND FabricVisualID = ?
            """,
            page_id,
            visual["fabric_visual_id"],
        )

        result = cursor.fetchone()

        if not result:

            raise RuntimeError(
                "Could not retrieve VisualID for visual: "
                f"{visual['fabric_visual_id']}"
            )

        visual_lookup[
            visual["fabric_visual_id"]
        ] = int(
            result[0]
        )

    cursor.connection.commit()

    logging.info(
        "Visuals loaded: %d",
        len(visual_lookup),
    )

    return visual_lookup


# ============================================================================
# RESOLVE FIELD IDS
# ============================================================================


def resolve_field_ids(
    field,
    semantic_model_id,
    semantic_table_lookup,
    semantic_column_lookup,
    measure_lookup,
):
    """Resolve repository IDs for a report field."""

    table_name = field.get(
        "table_name"
    )

    column_name = field.get(
        "column_name"
    )

    measure_name = field.get(
        "measure_name"
    )

    semantic_table_id = None
    semantic_column_id = None
    measure_id = None

    # ------------------------------------------------------------------------
    # Semantic table
    # ------------------------------------------------------------------------

    if table_name:

        semantic_table_id = (
            semantic_table_lookup.get(
                (
                    semantic_model_id,
                    str(table_name),
                )
            )
        )

    # ------------------------------------------------------------------------
    # Semantic column
    # ------------------------------------------------------------------------

    if (
        table_name
        and column_name
    ):

        semantic_column_id = (
            semantic_column_lookup.get(
                (
                    semantic_model_id,
                    str(table_name),
                    str(column_name),
                )
            )
        )

    # ------------------------------------------------------------------------
    # Measure
    # ------------------------------------------------------------------------

    if measure_name:

        measure_id = (
            measure_lookup.get(
                (
                    semantic_model_id,
                    str(measure_name),
                )
            )
        )

    return (
        semantic_table_id,
        semantic_column_id,
        measure_id,
    )


# ============================================================================
# LOAD VISUAL FIELDS
# ============================================================================


def load_visual_fields(
    cursor,
    visual_lookup,
    visuals,
    semantic_model_id,
    semantic_table_lookup,
    semantic_column_lookup,
    measure_lookup,
):

    inserted = 0

    unresolved = 0

    for visual in visuals:

        visual_id = visual_lookup.get(
            visual["fabric_visual_id"]
        )

        if visual_id is None:
            continue

        fields = visual.get(
            "fields",
            [],
        )

        for field in fields:

            (
                semantic_table_id,
                semantic_column_id,
                measure_id,
            ) = resolve_field_ids(
                field,
                semantic_model_id,
                semantic_table_lookup,
                semantic_column_lookup,
                measure_lookup,
            )

            if (
                field.get("table_name")
                and semantic_table_id is None
            ):

                unresolved += 1

                logging.warning(
                    "Could not resolve semantic table "
                    "'%s' for visual '%s'.",
                    field.get("table_name"),
                    visual["fabric_visual_id"],
                )

            if (
                field.get("column_name")
                and semantic_column_id is None
            ):

                unresolved += 1

                logging.warning(
                    "Could not resolve semantic column "
                    "'%s.%s' for visual '%s'.",
                    field.get("table_name"),
                    field.get("column_name"),
                    visual["fabric_visual_id"],
                )

            if (
                field.get("measure_name")
                and measure_id is None
            ):

                unresolved += 1

                logging.warning(
                    "Could not resolve measure "
                    "'%s' for visual '%s'.",
                    field.get("measure_name"),
                    visual["fabric_visual_id"],
                )

            cursor.execute(
                """
                INSERT INTO dbo.MetadataReportVisualField
                (
                    VisualID,
                    FieldType,
                    SemanticTableID,
                    SemanticColumnID,
                    MeasureID,
                    AggregationFunction,
                    ProjectionArea,
                    QueryRef,
                    NativeQueryRef
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                visual_id,
                field.get(
                    "field_type"
                ),
                semantic_table_id,
                semantic_column_id,
                measure_id,
                field.get(
                    "aggregation_function"
                ),
                field.get(
                    "projection_area"
                ),
                field.get(
                    "query_ref"
                ),
                field.get(
                    "native_query_ref"
                ),
            )

            inserted += 1

    cursor.connection.commit()

    logging.info(
        "Visual fields loaded: %d",
        inserted,
    )

    if unresolved:

        logging.warning(
            "Unresolved visual field references: %d",
            unresolved,
        )

    return inserted


# ============================================================================
# LOAD VISUAL FILTERS
# ============================================================================


def load_visual_filters(
    cursor,
    visual_lookup,
    visuals,
    semantic_model_id,
    semantic_table_lookup,
    semantic_column_lookup,
    measure_lookup,
):

    inserted = 0

    unresolved = 0

    for visual in visuals:

        visual_id = visual_lookup.get(
            visual["fabric_visual_id"]
        )

        if visual_id is None:
            continue

        filters = visual.get(
            "filters",
            [],
        )

        for filter_definition in filters:

            (
                semantic_table_id,
                semantic_column_id,
                measure_id,
            ) = resolve_field_ids(
                filter_definition,
                semantic_model_id,
                semantic_table_lookup,
                semantic_column_lookup,
                measure_lookup,
            )

            if (
                filter_definition.get("table_name")
                and semantic_table_id is None
            ):

                unresolved += 1

            if (
                filter_definition.get("column_name")
                and semantic_column_id is None
            ):

                unresolved += 1

            if (
                filter_definition.get("measure_name")
                and measure_id is None
            ):

                unresolved += 1

            cursor.execute(
                """
                INSERT INTO dbo.MetadataReportVisualFilter
                (
                    VisualID,
                    FilterName,
                    FieldType,
                    SemanticTableID,
                    SemanticColumnID,
                    MeasureID,
                    FilterType
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                visual_id,
                filter_definition.get(
                    "filter_name"
                ),
                filter_definition.get(
                    "field_type"
                ),
                semantic_table_id,
                semantic_column_id,
                measure_id,
                filter_definition.get(
                    "filter_type"
                ),
            )

            inserted += 1

    cursor.connection.commit()

    logging.info(
        "Visual filters loaded: %d",
        inserted,
    )

    if unresolved:

        logging.warning(
            "Unresolved visual filter references: %d",
            unresolved,
        )

    return inserted


# ============================================================================
# ENRICH VISUALS
# ============================================================================


def enrich_visuals(
    extractor,
    visuals,
):

    total_fields = 0

    total_filters = 0

    for visual in visuals:

        fields = extractor.extract_visual_fields(
            visual
        )

        filters = extractor.extract_visual_filters(
            visual
        )

        visual["fields"] = fields

        visual["filters"] = filters

        total_fields += len(
            fields
        )

        total_filters += len(
            filters
        )

    return (
        total_fields,
        total_filters,
    )


# ============================================================================
# PROCESS REPORT
# ============================================================================


def process_report(
    client,
    cursor,
    report,
):
    """Extract and load one Fabric report."""

    report_name = report[
        "name"
    ]

    report_id = report[
        "id"
    ]

    print()
    print("=" * 70)
    print(
        f"REPORT: {report_name}"
    )
    print("=" * 70)

    print(
        f"Fabric Report ID: {report_id}"
    )

    # ========================================================================
    # 1. GET REPORT DEFINITION
    # ========================================================================

    print()
    print(
        "Retrieving report definition..."
    )

    definition = client.get_report_definition(
        WORKSPACE_ID,
        report_id,
    )

    if not definition:

        raise RuntimeError(
            "Empty report definition returned."
        )

    save_report_definition(
        report_name,
        definition,
    )

    # ========================================================================
    # 2. RESOLVE SEMANTIC MODEL
    # ========================================================================

    print()
    print(
        "Resolving connected semantic model..."
    )

    fabric_semantic_model_id = (
        resolve_semantic_model_for_report(
            client,
            WORKSPACE_ID,
            report,
            definition,
            cursor,
        )
    )

    print(
        "Fabric Semantic Model ID: "
        f"{fabric_semantic_model_id}"
    )

    # ========================================================================
    # 3. RESOLVE REPOSITORY SEMANTIC MODEL
    # ========================================================================

    repository_semantic_model_id = (
        get_repository_semantic_model_id(
            cursor,
            fabric_semantic_model_id,
        )
    )

    print(
        "Repository SemanticModelID: "
        f"{repository_semantic_model_id}"
    )

    # ========================================================================
    # 4. CREATE / UPDATE REPORT
    # ========================================================================

    repository_report_id = (
        get_or_create_report(
            cursor,
            report_name,
            report_id,
            repository_semantic_model_id,
        )
    )

    print(
        "Repository ReportID: "
        f"{repository_report_id}"
    )

    # ========================================================================
    # 5. PARSE REPORT
    # ========================================================================

    extractor = ReportMetadataExtractor(
        definition
    )

    pages = extractor.extract_pages()

    visuals = extractor.extract_visuals()

    (
        extracted_fields,
        extracted_filters,
    ) = enrich_visuals(
        extractor,
        visuals,
    )

    print()
    print(
        "Refreshing report child metadata..."
    )

    # ========================================================================
    # 6. CLEAR PREVIOUS CHILDREN
    # ========================================================================

    clear_report_children(
        cursor,
        repository_report_id,
    )

    # ========================================================================
    # 7. LOAD PAGES
    # ========================================================================

    page_lookup = load_pages(
        cursor,
        repository_report_id,
        pages,
    )

    # ========================================================================
    # 8. LOAD VISUALS
    # ========================================================================

    visual_lookup = load_visuals(
        cursor,
        page_lookup,
        visuals,
    )

    # ========================================================================
    # 9. BUILD SEMANTIC LOOKUPS
    # ========================================================================

    semantic_table_lookup = (
        build_semantic_table_lookup(
            cursor
        )
    )

    semantic_column_lookup = (
        build_semantic_column_lookup(
            cursor
        )
    )

    measure_lookup = (
        build_measure_lookup(
            cursor
        )
    )

    # ========================================================================
    # 10. LOAD VISUAL FIELDS
    # ========================================================================

    loaded_fields = load_visual_fields(
        cursor,
        visual_lookup,
        visuals,
        repository_semantic_model_id,
        semantic_table_lookup,
        semantic_column_lookup,
        measure_lookup,
    )

    # ========================================================================
    # 11. LOAD VISUAL FILTERS
    # ========================================================================

    loaded_filters = load_visual_filters(
        cursor,
        visual_lookup,
        visuals,
        repository_semantic_model_id,
        semantic_table_lookup,
        semantic_column_lookup,
        measure_lookup,
    )

    # ========================================================================
    # 12. SUMMARY
    # ========================================================================

    print()
    print(
        f"Completed report: {report_name}"
    )

    print(
        f"  Pages:   {len(pages)}"
    )

    print(
        f"  Visuals: {len(visuals)}"
    )

    print(
        f"  Fields:  {loaded_fields}"
    )

    print(
        f"  Filters: {loaded_filters}"
    )

    if extracted_fields != loaded_fields:

        logging.warning(
            "Extracted fields (%d) != loaded fields (%d).",
            extracted_fields,
            loaded_fields,
        )

    if extracted_filters != loaded_filters:

        logging.warning(
            "Extracted filters (%d) != loaded filters (%d).",
            extracted_filters,
            loaded_filters,
        )

    return {
        "report_name": report_name,
        "report_id": report_id,
        "pages": len(pages),
        "visuals": len(visuals),
        "fields": loaded_fields,
        "filters": loaded_filters,
    }


# ============================================================================
# MAIN
# ============================================================================


def main():

    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
    )

    print("=" * 70)
    print(
        "FABRIC REPORT METADATA EXTRACTOR"
    )
    print("=" * 70)

    repository_connection = None

    successful = []

    failed = []

    total_pages = 0

    total_visuals = 0

    total_fields = 0

    total_filters = 0

    try:

        # ====================================================================
        # 1. FABRIC CLIENT
        # ====================================================================

        print()
        print(
            "Initializing Fabric REST API client..."
        )

        client = FabricClient()

        # ====================================================================
        # 2. WORKSPACE
        # ====================================================================

        print()
        print(
            "Discovering workspace..."
        )

        workspace = client.get_workspace(
            WORKSPACE_ID
        )

        if isinstance(
            workspace,
            dict,
        ):

            actual_workspace_name = (
                workspace.get(
                    "displayName"
                )
                or workspace.get(
                    "name"
                )
                or WORKSPACE_NAME
            )

        else:

            actual_workspace_name = (
                WORKSPACE_NAME
            )

        print(
            f"Workspace: {actual_workspace_name}"
        )

        # ====================================================================
        # 3. REPORT DISCOVERY
        # ====================================================================

        print()
        print(
            "Discovering reports automatically..."
        )

        reports = discover_reports(
            client,
            WORKSPACE_ID,
        )

        print(
            f"Reports discovered: {len(reports)}"
        )

        if not reports:

            print()
            print(
                "No reports were found in the workspace."
            )

            return 0

        print()
        print(
            "Discovered reports:"
        )

        for index, report in enumerate(
            reports,
            start=1,
        ):

            print(
                f"  {index}. "
                f"{report['name']} | "
                f"ID: {report['id']}"
            )

        # ====================================================================
        # 4. CONNECT TO METADATA REPOSITORY
        # ====================================================================

        print()
        print("=" * 70)
        print(
            "CONNECTING TO METADATA REPOSITORY"
        )
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

        cursor = repository_connection.cursor()

        print()
        print(
            "Connected to MetadataRepository successfully."
        )

        # ====================================================================
        # 5. PROCESS REPORTS
        # ====================================================================

        for report in reports:

            try:

                result = process_report(
                    client,
                    cursor,
                    report,
                )

                successful.append(
                    result
                )

                total_pages += result[
                    "pages"
                ]

                total_visuals += result[
                    "visuals"
                ]

                total_fields += result[
                    "fields"
                ]

                total_filters += result[
                    "filters"
                ]

            except Exception as exc:

                failed.append(
                    {
                        "report": report,
                        "error": str(exc),
                    }
                )

                logging.exception(
                    "Failed processing report '%s'.",
                    report["name"],
                )

                print()
                print(
                    f"ERROR processing report "
                    f"'{report['name']}': {exc}"
                )

                continue

        # ====================================================================
        # 6. FINAL SUMMARY
        # ====================================================================

        print()
        print("=" * 70)
        print(
            "REPORT METADATA EXTRACTION COMPLETED"
        )
        print("=" * 70)

        print(
            f"Reports discovered:  {len(reports)}"
        )

        print(
            f"Reports successful:  {len(successful)}"
        )

        print(
            f"Reports failed:      {len(failed)}"
        )

        print(
            f"Pages extracted:     {total_pages}"
        )

        print(
            f"Visuals extracted:   {total_visuals}"
        )

        print(
            f"Fields extracted:    {total_fields}"
        )

        print(
            f"Filters extracted:   {total_filters}"
        )

        if failed:

            print()
            print(
                "FAILED REPORTS"
            )
            print(
                "-" * 70
            )

            for failure in failed:

                print(
                    f"- {failure['report']['name']}: "
                    f"{failure['error']}"
                )

        if successful:

            print()
            print(
                "Metadata successfully loaded into "
                "MetadataRepository."
            )

        return (
            0
            if not failed
            else 1
        )

    except Exception as exc:

        logging.exception(
            "Report metadata extraction failed."
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


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":

    raise SystemExit(
        main()
    )

