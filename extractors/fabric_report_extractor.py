# FULL CORRECTED REPORT EXTRACTOR V2
#
# Key filter correction:
#   - Visual fields/projections are NOT automatically treated as filters.
#   - Only actual persisted filter definitions are extracted.
#   - TopN and Advanced filters are preserved.
#   - PBIR visual filterConfig is read from the visual.json root.
#   - Page/report filterConfig is recognized separately where supported.
#   - _parse_filter_field(), _has_filter_target() and all filter helpers
#     are instance methods, avoiding the previous TypeError.
#
# Existing field extraction, semantic model resolution, batch loading,
# Unicode handling, and lineage logic are preserved.


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

logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION
# ============================================================================

WORKSPACE_CONFIG_FILE = (
    ROOT_DIR / "config" / "workspaces.json"
)


# ============================================================================
# FABRIC WAREHOUSE
# ============================================================================

FABRIC_SQL_SERVER = (
    "j7mjaqg22d2ujb27llpciiyism-7jnw46tiqcde5cpv233ctk345u.datawarehouse.fabric.microsoft.com"
)

FABRIC_SQL_DATABASE = "MetadataRepository"

# -------------------------------------------------------------------------
# DEBUG / DEVELOPMENT SETTING
# Set to a report name to process only that report.
# Set to None to process all reports.
# -------------------------------------------------------------------------

TARGET_REPORT_NAME = None


# ============================================================================
# OUTPUT
# ============================================================================

OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# UUID
# ============================================================================

UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)


def is_uuid(value):

    if value is None:
        return False

    return bool(UUID_PATTERN.match(str(value)))


# ============================================================================
# GENERIC HELPERS
# ============================================================================

def unique_preserve_order(values):

    result = []

    for value in values:

        if value is None:
            continue

        value = str(value)

        if value not in result:
            result.append(value)

    return result


def safe_string(value):

    if value is None:
        return None

    return str(value).strip()


def normalize_name(value):

    if value is None:
        return ""

    value = str(value).lower()

    value = re.sub(
        r"[^a-z0-9]+",
        "",
        value,
    )

    return value


def tokenize_name(value):

    if value is None:
        return set()

    value = str(value)

    tokens = re.findall(
        r"[A-Za-z0-9]+",
        value.lower(),
    )

    return {
        token
        for token in tokens
        if token
    }


def meaningful_tokens(value):

    stop_words = {
        "sm",
        "semantic",
        "model",
        "dataset",
        "report",
        "overview",
        "dashboard",
        "powerbi",
        "power",
        "bi",
    }

    return {
        token
        for token in tokenize_name(value)
        if token not in stop_words
    }


def name_similarity_score(
    report_name,
    model_name,
):

    report_tokens = meaningful_tokens(
        report_name
    )

    model_tokens = meaningful_tokens(
        model_name
    )

    if not report_tokens or not model_tokens:
        return 0

    intersection = (
        report_tokens
        & model_tokens
    )

    if not intersection:
        return 0

    score = 0

    score += len(intersection) * 100

    normalized_report = normalize_name(
        report_name
    )

    normalized_model = normalize_name(
        model_name
    )

    for token in intersection:

        normalized_token = normalize_name(
            token
        )

        if normalized_token in normalized_report:
            score += 25

        if normalized_token in normalized_model:
            score += 25

    return score


# ============================================================================
# FABRIC WAREHOUSE CONNECTION
# ============================================================================

def get_fabric_connection_string(
    driver,
    server,
    database,
):

    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Authentication=ActiveDirectoryInteractive;"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "LongAsMax=yes;"
    )


def _decode_utf8_column(raw_bytes):

    if raw_bytes is None:
        return None

    return raw_bytes.decode("utf-8")


def connect_to_fabric_warehouse(
    driver,
    server,
    database,
):

    connection_string = get_fabric_connection_string(
        driver,
        server,
        database,
    )

    logger.info(
        "Opening Microsoft Entra interactive authentication..."
    )

    connection = pyodbc.connect(
        connection_string
    )

    connection.add_output_converter(
        pyodbc.SQL_CHAR,
        _decode_utf8_column,
    )

    connection.add_output_converter(
        pyodbc.SQL_VARCHAR,
        _decode_utf8_column,
    )

    return connection


def is_connection_failure(error):

    connection_error_codes = (
        "08S01",
        "08003",
        "08006",
        "08007",
    )

    error_text = str(error)

    return any(
        code in error_text
        for code in connection_error_codes
    )


def rollback_transaction(
    connection,
    report_name,
):

    try:

        connection.rollback()

    except Exception as rollback_error:

        logger.warning(
            "Could not roll back report transaction for '%s': %s",
            report_name,
            rollback_error,
        )


def execute_insert_batches(
    cursor,
    statement,
    rows,
):

    cursor.fast_executemany = True

    cursor.executemany(
        statement,
        rows,
    )


# ============================================================================
# WORKSPACE CONFIGURATION
# ============================================================================

def load_enabled_workspaces():

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
        []
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
# WORKSPACE DISCOVERY
# ============================================================================

def normalize_workspace_items(response):

    if isinstance(response, dict):

        value = response.get(
            "value"
        )

        if isinstance(value, list):
            return value

        items = response.get(
            "items"
        )

        if isinstance(items, list):
            return items

        raise RuntimeError(
            "Unexpected workspace-items response format. "
            f"Dictionary keys: {list(response.keys())}"
        )

    if isinstance(response, list):
        return response

    raise RuntimeError(
        "Unexpected workspace-items response type: "
        f"{type(response).__name__}"
    )


def get_workspace_items(
    client,
    workspace_id,
):

    response = client.get_workspace_items(
        workspace_id
    )

    items = normalize_workspace_items(
        response
    )

    return [
        item
        for item in items
        if isinstance(item, dict)
    ]


def discover_reports_from_items(
    workspace_items,
):

    reports = []

    for item in workspace_items:

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

        if not report_id:

            logger.warning(
                "Skipping report without ID: %s",
                item,
            )

            continue

        report_name = (
            item.get("displayName")
            or item.get("name")
            or item.get("reportName")
            or str(report_id)
        )

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

    logger.info(
        "Reports discovered: %d",
        len(reports),
    )

    return reports


def discover_semantic_models(
    workspace_items,
):

    models = []

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

        if not model_id:
            continue

        model_name = (
            item.get("displayName")
            or item.get("name")
            or item.get("modelName")
            or str(model_id)
        )

        models.append(
            {
                "id": str(model_id),
                "name": str(model_name),
                "raw": item,
            }
        )

    models.sort(
        key=lambda x: x["name"].lower()
    )

    return models


def print_workspace_inventory(
    workspace_items,
):

    logger.info(
        "Workspace inventory:"
    )

    item_types = {}

    for item in workspace_items:

        item_type = item.get(
            "type",
            "Unknown",
        )

        item_types.setdefault(
            item_type,
            0,
        )

        item_types[item_type] += 1

    for item_type, count in sorted(
        item_types.items()
    ):

        logger.info(
            "  %s=%d",
            item_type,
            count,
        )


# ============================================================================
# DEFINITION HELPERS
# ============================================================================

def get_definition_parts(
    definition,
):

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

    if not isinstance(
        definition_object,
        dict,
    ):

        return []

    parts = definition_object.get(
        "parts",
        [],
    )

    if not isinstance(
        parts,
        list,
    ):

        return []

    return parts


def decode_definition_part(
    part,
):

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

        logger.exception(
            "Could not decode definition part: %s",
            part.get("path"),
        )

        return ""


def parse_json_part(
    part,
):

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

        return None


def save_report_definition(
    report_name,
    definition,
):

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

        logger.info(
            "Saved report definition: %s",
            output_file,
        )

    except Exception:

        logger.exception(
            "Could not save report definition."
        )


# ============================================================================
# SEMANTIC MODEL DISCOVERY FROM REPORT
# ============================================================================

SEMANTIC_ID_KEYS = {
    "semanticmodelid",
    "semanticmodel_id",
    "semanticmodelguid",
    "datasetid",
    "dataset_id",
    "datasetguid",
    "modelid",
    "model_id",
    "modelguid",
}


def collect_ids_from_key(
    key,
    value,
    candidates,
):

    key_normalized = re.sub(
        r"[^a-z0-9]",
        "",
        str(key).lower(),
    )

    is_model_key = (
        (
            "semanticmodel" in key_normalized
            and "id" in key_normalized
        )
        or (
            "dataset" in key_normalized
            and "id" in key_normalized
        )
    )

    # ---------------------------------------------------------
    # 1. Normal JSON model/dataset ID fields
    # ---------------------------------------------------------
    if is_model_key:

        if (
            isinstance(value, str)
            and is_uuid(value)
        ):

            candidates.append(
                value
            )

        elif isinstance(
            value,
            dict,
        ):

            for nested_key in (
                "id",
                "modelId",
                "modelID",
                "semanticModelId",
                "semanticModelID",
                "datasetId",
                "datasetID",
            ):

                nested_value = value.get(
                    nested_key
                )

                if is_uuid(
                    nested_value
                ):

                    candidates.append(
                        str(nested_value)
                    )

    # ---------------------------------------------------------
    # 2. PBIR connection strings
    #
    # Example:
    # semanticmodelid=57540ac5-bb7e-4b05-93f4-e7e6e036553d
    # ---------------------------------------------------------
    if isinstance(
        value,
        str,
    ):

        patterns = (
            r"semanticmodelid\s*=\s*([0-9a-fA-F-]{36})",
            r"datasetid\s*=\s*([0-9a-fA-F-]{36})",
            r"modelid\s*=\s*([0-9a-fA-F-]{36})",
        )

        for pattern in patterns:

            matches = re.findall(
                pattern,
                value,
                flags=re.IGNORECASE,
            )

            for match in matches:

                if is_uuid(
                    match
                ):

                    candidates.append(
                        match
                    )


def collect_semantic_model_candidates(
    value,
    candidates=None,
):

    if candidates is None:
        candidates = []

    if isinstance(
        value,
        dict,
    ):

        for key, child in value.items():

            collect_ids_from_key(
                key,
                child,
                candidates,
            )

            collect_semantic_model_candidates(
                child,
                candidates,
            )

    elif isinstance(
        value,
        list,
    ):

        for child in value:

            collect_semantic_model_candidates(
                child,
                candidates,
            )

    return candidates


def extract_report_semantic_model_ids(
    definition,
):

    candidates = []

    for part in get_definition_parts(
        definition
    ):

        data = parse_json_part(
            part
        )

        if data is None:
            continue

        candidates.extend(
            collect_semantic_model_candidates(
                data
            )
        )

    return unique_preserve_order(
        candidates
    )


def extract_semantic_model_ids_from_workspace_item(
    report_item,
):

    if not isinstance(
        report_item,
        dict,
    ):

        return []

    candidates = (
        collect_semantic_model_candidates(
            report_item
        )
    )

    return unique_preserve_order(
        [
            candidate
            for candidate in candidates
            if is_uuid(candidate)
        ]
    )


# ============================================================================
# REPOSITORY LOOKUPS
# ============================================================================

def get_repository_semantic_models(
    cursor,
):

    cursor.execute(
        """
        SELECT
            SemanticModelID,
            ModelName,
            FabricModelID
        FROM dbo.MetadataSemanticModel
        """
    )

    models = []

    for row in cursor.fetchall():

        repository_id = row[0]
        model_name = row[1]
        fabric_model_id = row[2]

        models.append(
            {
                "repository_id": int(
                    repository_id
                ),
                "name": (
                    str(model_name)
                    if model_name is not None
                    else ""
                ),
                "fabric_id": (
                    str(fabric_model_id)
                    if fabric_model_id is not None
                    else None
                ),
            }
        )

    return models


def get_repository_semantic_model_id(
    cursor,
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
# INTELLIGENT SEMANTIC MODEL RESOLUTION
# ============================================================================

def score_semantic_model_candidate(
    report,
    model,
    repository_model=None,
):

    report_name = report["name"]

    fabric_model_name = model.get(
        "name",
        "",
    )

    score = name_similarity_score(
        report_name,
        fabric_model_name,
    )

    reasons = []

    if score > 0:

        shared_tokens = (
            meaningful_tokens(
                report_name
            )
            & meaningful_tokens(
                fabric_model_name
            )
        )

        if shared_tokens:

            reasons.append(
                "shared tokens: "
                + ", ".join(
                    sorted(shared_tokens)
                )
            )

    if repository_model:

        repository_name = (
            repository_model.get(
                "name"
            )
            or ""
        )

        repository_score = (
            name_similarity_score(
                report_name,
                repository_name,
            )
        )

        if repository_score > 0:

            score += repository_score

            reasons.append(
                "repository model name match"
            )

    normalized_report = normalize_name(
        report_name
    )

    normalized_model = normalize_name(
        fabric_model_name
    )

    if (
        normalized_report
        and normalized_model
        and (
            normalized_report
            == normalized_model
        )
    ):

        score += 500

        reasons.append(
            "exact normalized name match"
        )

    return score, reasons


def resolve_semantic_model_for_report(
    client,
    workspace_id,
    report,
    definition,
    cursor,
    workspace_items,
    repository_models_cache,
):

    report_name = report["name"]

    semantic_models = discover_semantic_models(
        workspace_items
    )

    workspace_model_ids = {
        model["id"]
        for model in semantic_models
    }

    logger.info(
        "Semantic models discovered in workspace: %d",
        len(semantic_models),
    )

    definition_candidates = (
        extract_report_semantic_model_ids(
            definition
        )
    )

    logger.info(
        "Semantic model candidates found in report definition: %s",
        definition_candidates or "none",
    )

    valid_definition_candidates = [
        candidate
        for candidate in definition_candidates
        if candidate in workspace_model_ids
    ]

    if len(valid_definition_candidates) == 1:

        selected = (
            valid_definition_candidates[0]
        )

        selected_model = next(
            model
            for model in semantic_models
            if model["id"] == selected
        )

        logger.info(
            "Semantic model resolved from explicit report definition: "
            "%s [%s]",
            selected_model["name"],
            selected,
        )

        return selected

    if len(valid_definition_candidates) > 1:

        logger.warning(
            "Multiple explicit semantic model candidates were found "
            "in report definition. Continuing with scoring."
        )

    report_item = report.get(
        "raw"
    )

    workspace_candidates = (
        extract_semantic_model_ids_from_workspace_item(
            report_item
        )
    )

    logger.info(
        "Semantic model candidates found in report workspace metadata: %s",
        workspace_candidates or "none",
    )

    valid_workspace_candidates = [
        candidate
        for candidate in workspace_candidates
        if candidate in workspace_model_ids
    ]

    if len(valid_workspace_candidates) == 1:

        selected = (
            valid_workspace_candidates[0]
        )

        selected_model = next(
            model
            for model in semantic_models
            if model["id"] == selected
        )

        logger.info(
            "Semantic model resolved from report workspace metadata: "
            "%s [%s]",
            selected_model["name"],
            selected,
        )

        return selected

    if len(valid_workspace_candidates) > 1:

        logger.warning(
            "Multiple semantic model candidates were found in "
            "report workspace metadata. Continuing with scoring."
        )

    repository_by_fabric_id = {
        model["fabric_id"]: model
        for model in repository_models_cache
        if model["fabric_id"]
    }

    repository_definition_matches = [
        candidate
        for candidate in definition_candidates
        if candidate in repository_by_fabric_id
    ]

    if len(repository_definition_matches) == 1:

        selected = (
            repository_definition_matches[0]
        )

        logger.info(
            "Semantic model resolved using repository cross-reference: "
            "%s",
            selected,
        )

        return selected

    logger.info(
        "Attempting intelligent report-to-semantic-model matching..."
    )

    scored_candidates = []

    for model in semantic_models:

        repository_model = (
            repository_by_fabric_id.get(
                model["id"]
            )
        )

        score, reasons = (
            score_semantic_model_candidate(
                report,
                model,
                repository_model,
            )
        )

        scored_candidates.append(
            {
                "model": model,
                "score": score,
                "reasons": reasons,
            }
        )

    scored_candidates.sort(
        key=lambda candidate: candidate["score"],
        reverse=True,
    )

    logger.info(
        "Semantic model candidate scores for report '%s':",
        report_name,
    )

    for candidate in scored_candidates:

        model = candidate["model"]

        logger.info(
            "  %s [%s] -> score=%d | %s",
            model["name"],
            model["id"],
            candidate["score"],
            (
                ", ".join(
                    candidate["reasons"]
                )
                if candidate["reasons"]
                else "no name evidence"
            ),
        )

    if scored_candidates:

        best = scored_candidates[0]

        if best["score"] > 0:

            if len(scored_candidates) == 1:

                logger.info(
                    "Semantic model resolved by name scoring: "
                    "%s [%s]",
                    best["model"]["name"],
                    best["model"]["id"],
                )

                return best["model"]["id"]

            second = scored_candidates[1]

            score_difference = (
                best["score"]
                - second["score"]
            )

            if (
                best["score"] >= 100
                and score_difference >= 50
            ):

                logger.info(
                    "Semantic model resolved by intelligent matching: "
                    "%s [%s]",
                    best["model"]["name"],
                    best["model"]["id"],
                )

                logger.info(
                    "Match confidence: score=%d, second=%d, "
                    "difference=%d",
                    best["score"],
                    second["score"],
                    score_difference,
                )

                return best["model"]["id"]

            logger.warning(
                "Name matching produced an ambiguous result. "
                "Best candidate=%s score=%d, "
                "second candidate=%s score=%d.",
                best["model"]["name"],
                best["score"],
                second["model"]["name"],
                second["score"],
            )

    if len(semantic_models) == 1:

        model = semantic_models[0]

        logger.warning(
            "Only one semantic model exists in the workspace. "
            "Using it for report '%s': %s (%s)",
            report_name,
            model["name"],
            model["id"],
        )

        return model["id"]

    available_models = ", ".join(
        f"{model['name']} [{model['id']}"
        "]"
        for model in semantic_models
    )

    raise RuntimeError(
        "Could not safely determine the semantic model for "
        f"report '{report_name}'. "
        f"Available semantic models: "
        f"{available_models or 'none'}"
    )


# ============================================================================
# REPORT METADATA EXTRACTOR
# ============================================================================

class ReportMetadataExtractor:

    def __init__(
        self,
        definition,
    ):

        self.definition = definition

        self.parts = get_definition_parts(
            definition
        )

        self.report_format = (
            self._detect_report_format()
        )

        logger.info(
            "Report definition format detected: %s",
            self.report_format,
        )

    # ========================================================================
    # FORMAT DETECTION
    # ========================================================================

    def _detect_report_format(self):

        paths = {
            str(
                part.get(
                    "path",
                    "",
                )
            ).replace(
                "\\",
                "/",
            )
            for part in self.parts
            if isinstance(
                part,
                dict,
            )
        }

        if any(
            path.startswith(
                "definition/pages/"
            )
            for path in paths
        ):

            return "PBIR"

        if any(
            path.startswith(
                "definition/"
            )
            for path in paths
        ):

            return "PBIR"

        if "report.json" in paths:

            return "PBIR-Legacy"

        logger.warning(
            "Could not confidently detect report definition format. "
            "Falling back to PBIR parser."
        )

        return "PBIR"

    # ========================================================================
    # PART HELPERS
    # ========================================================================

    def _find_part(
        self,
        path,
    ):

        normalized_path = str(
            path
        ).replace(
            "\\",
            "/",
        )

        for part in self.parts:

            part_path = str(
                part.get(
                    "path",
                    "",
                )
            ).replace(
                "\\",
                "/",
            )

            if part_path == normalized_path:
                return part

        return None

    @staticmethod
    def _parse_json_value(
        value,
        default=None,
    ):

        if value is None:
            return default

        if isinstance(
            value,
            (
                dict,
                list,
            ),
        ):

            return value

        if isinstance(
            value,
            str,
        ):

            value = value.strip()

            if not value:
                return default

            try:

                return json.loads(
                    value
                )

            except (
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ):

                return default

        return default

    def _get_legacy_report(self):

        part = self._find_part(
            "report.json"
        )

        if part is None:

            logger.warning(
                "PBIR-Legacy report.json part was not found."
            )

            return None

        data = parse_json_part(
            part
        )

        if not isinstance(
            data,
            dict,
        ):

            logger.warning(
                "PBIR-Legacy report.json could not be parsed."
            )

            return None

        return data

    # ========================================================================
    # PAGES
    # ========================================================================

    def extract_pages(self):

        if self.report_format == "PBIR-Legacy":

            return self._extract_legacy_pages()

        return self._extract_pbir_pages()

    def _extract_pbir_pages(self):

        pages = []
        page_order = []

        for part in self.parts:

            path = str(
                part.get(
                    "path",
                    "",
                )
            ).replace(
                "\\",
                "/",
            )

            if path != "definition/pages/pages.json":
                continue

            data = parse_json_part(
                part
            )

            if not isinstance(
                data,
                dict,
            ):

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

        page_data = {}

        for part in self.parts:

            path = str(
                part.get(
                    "path",
                    "",
                )
            ).replace(
                "\\",
                "/",
            )

            match = re.match(
                r"^definition/pages/([^/]+)/page\.json$",
                path,
            )

            if not match:
                continue

            data = parse_json_part(
                part
            )

            if not isinstance(
                data,
                dict,
            ):

                continue

            page_id = (
                data.get("name")
                or match.group(1)
            )

            page_data[
                str(page_id)
            ] = data

        ordered_ids = []

        for page_id in page_order:

            page_id = str(
                page_id
            )

            if (
                page_id in page_data
                and page_id not in ordered_ids
            ):

                ordered_ids.append(
                    page_id
                )

        for page_id in page_data:

            if page_id not in ordered_ids:

                ordered_ids.append(
                    page_id
                )

        for index, page_id in enumerate(
            ordered_ids,
            start=1,
        ):

            data = page_data[
                page_id
            ]

            pages.append(
                {
                    "page_name": page_id,
                    "display_name": (
                        data.get(
                            "displayName"
                        )
                        or page_id
                    ),
                    "page_order": index,
                }
            )

        logger.info(
            "PBIR pages discovered: %d",
            len(pages),
        )

        return pages

    def _extract_legacy_pages(self):

        pages = []

        report = self._get_legacy_report()

        if not report:
            return pages

        sections = report.get(
            "sections",
            []
        )

        if not isinstance(
            sections,
            list,
        ):

            logger.warning(
                "PBIR-Legacy report.json contains no valid sections array."
            )

            return pages

        logger.info(
            "Legacy report sections discovered: %d",
            len(sections),
        )

        for index, section in enumerate(
            sections,
            start=1,
        ):

            if not isinstance(
                section,
                dict,
            ):

                continue

            page_name = (
                section.get("name")
                or section.get("id")
                or f"Page_{index}"
            )

            display_name = (
                section.get("displayName")
                or section.get("displayNameExpression")
                or page_name
            )

            ordinal = section.get(
                "ordinal"
            )

            if isinstance(
                ordinal,
                (
                    int,
                    float,
                ),
            ):

                page_order = int(
                    ordinal
                ) + 1

            else:

                page_order = index

            pages.append(
                {
                    "page_name": str(
                        page_name
                    ),
                    "display_name": str(
                        display_name
                    ),
                    "page_order": page_order,
                }
            )

        pages.sort(
            key=lambda page:
            page.get(
                "page_order",
                999999,
            )
        )

        for index, page in enumerate(
            pages,
            start=1,
        ):

            page["page_order"] = index

        logger.info(
            "PBIR-Legacy pages extracted: %d",
            len(pages),
        )

        return pages

    # ========================================================================
    # VISUALS
    # ========================================================================

    def extract_visuals(self):

        if self.report_format == "PBIR-Legacy":

            return self._extract_legacy_visuals()

        return self._extract_pbir_visuals()

    def _extract_pbir_visuals(self):

        visuals = []

        for part in self.parts:

            path = str(
                part.get(
                    "path",
                    "",
                )
            ).replace(
                "\\",
                "/",
            )

            match = re.match(
                r"^definition/pages/([^/]+)/visuals/([^/]+)/visual\.json$",
                path,
            )

            if not match:
                continue

            page_name = match.group(1)

            visual_id_from_path = match.group(2)

            data = parse_json_part(
                part
            )

            if not isinstance(
                data,
                dict,
            ):

                continue

            visual_id = (
                data.get("name")
                or visual_id_from_path
            )

            visual_definition = data.get(
                "visual",
                {},
            )

            if not isinstance(
                visual_definition,
                dict,
            ):

                visual_definition = {}

            visual_type = (
                visual_definition.get(
                    "visualType"
                )
                or data.get(
                    "visualType"
                )
                or "Unknown"
            )

            visuals.append(
                {
                    "page_name": page_name,
                    "fabric_visual_id": str(
                        visual_id
                    ),
                    "visual_type": str(
                        visual_type
                    ),
                    "definition_path": path,
                    "raw": data,
                }
            )

        logger.info(
            "PBIR visuals discovered: %d",
            len(visuals),
        )

        return visuals

    def _extract_legacy_visuals(self):

        visuals = []

        report = self._get_legacy_report()

        if not report:
            return visuals

        sections = report.get(
            "sections",
            []
        )

        if not isinstance(
            sections,
            list,
        ):

            return visuals

        for section_index, section in enumerate(
            sections,
            start=1,
        ):

            if not isinstance(
                section,
                dict,
            ):

                continue

            page_name = (
                section.get("name")
                or section.get("id")
                or f"Page_{section_index}"
            )

            visual_containers = (
                section.get(
                    "visualContainers",
                    []
                )
            )

            if not isinstance(
                visual_containers,
                list,
            ):

                continue

            for visual_index, container in enumerate(
                visual_containers,
                start=1,
            ):

                if not isinstance(
                    container,
                    dict,
                ):

                    continue

                visual_id = (
                    container.get("id")
                    or container.get("name")
                    or (
                        f"{page_name}_visual_"
                        f"{visual_index}"
                    )
                )

                config = self._parse_json_value(
                    container.get(
                        "config"
                    ),
                    default={},
                )

                if not isinstance(
                    config,
                    dict,
                ):

                    config = {}

                single_visual = config.get(
                    "singleVisual",
                    {},
                )

                if not isinstance(
                    single_visual,
                    dict,
                ):

                    single_visual = {}

                visual_type = (
                    single_visual.get(
                        "visualType"
                    )
                    or config.get(
                        "visualType"
                    )
                    or container.get(
                        "visualType"
                    )
                    or "Unknown"
                )

                query = self._parse_json_value(
                    container.get(
                        "query"
                    ),
                    default={},
                )

                if not isinstance(
                    query,
                    dict,
                ):

                    query = {}

                legacy_filters = (
                    self._parse_json_value(
                        container.get(
                            "filters"
                        ),
                        default=[],
                    )
                )

                if isinstance(
                    legacy_filters,
                    dict,
                ):

                    legacy_filters = [
                        legacy_filters
                    ]

                elif not isinstance(
                    legacy_filters,
                    list,
                ):

                    legacy_filters = []

                normalized_raw = {
                    "name": str(
                        visual_id
                    ),
                    "visual": {
                        "visualType": visual_type,
                        "query": query,
                        "singleVisual": single_visual,
                    },
                    "filterConfig": {
                        "filters": legacy_filters,
                    },
                    "legacyConfig": config,
                    "legacyQuery": query,
                    "legacyFilters": legacy_filters,
                    "position": {
                        "x": container.get("x"),
                        "y": container.get("y"),
                        "z": container.get("z"),
                        "width": container.get("width"),
                        "height": container.get("height"),
                    },
                }

                visuals.append(
                    {
                        "page_name": str(
                            page_name
                        ),
                        "fabric_visual_id": str(
                            visual_id
                        ),
                        "visual_type": str(
                            visual_type
                        ),
                        "definition_path": "report.json",
                        "raw": normalized_raw,
                    }
                )

        logger.info(
            "PBIR-Legacy visuals discovered: %d",
            len(visuals),
        )

        return visuals

    # ========================================================================
    # VISUAL FIELDS
    # ========================================================================

    def extract_visual_fields(
        self,
        visual,
    ):

        if self.report_format == "PBIR-Legacy":

            return self._extract_legacy_visual_fields(
                visual
            )

        return self._extract_pbir_visual_fields(
            visual
        )

    def _extract_pbir_visual_fields(
        self,
        visual,
    ):

        result = []

        raw = visual.get(
            "raw",
            {}
        )

        visual_definition = raw.get(
            "visual",
            {}
        )

        if not isinstance(
            visual_definition,
            dict,
        ):

            return result

        query = visual_definition.get(
            "query",
            {}
        )

        if not isinstance(
            query,
            dict,
        ):

            return result

        query_state = query.get(
            "queryState",
            {}
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
                []
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
                    {}
                )

                field_metadata = self._parse_field(
                    field
                )
                # -------------------------------------------------------------------------
                # Legacy aggregation / summarization
                #
                # NativeReferenceName contains the user-facing Power BI field name,
                # including the selected summarization, e.g.:
                #
                #     "First Country"
                #
                # while the internal query may contain:
                #
                #     "Min(dim_LegalEntity.Country)"
                #
                # The internal Function / Name must therefore NOT be treated as the
                # user-facing aggregation when NativeReferenceName is available.
                # -------------------------------------------------------------------------

                if (
                    field_metadata
                    and field_metadata.get("field_type") == "Column"
                ):

                    native_reference_name = select_item.get(
                        "NativeReferenceName"
                    )

                    if isinstance(
                        native_reference_name,
                        str,
                    ):

                        native_reference_name = (
                            native_reference_name.strip()
                        )

                        aggregation_match = re.match(
                            r"^\s*(First|Last|Sum|Average|Min|Max|Count|CountRows|DistinctCount)\b",
                            native_reference_name,
                            re.IGNORECASE,
                        )

                        if aggregation_match:

                            field_metadata[
                                "aggregation_function"
                            ] = aggregation_match.group(
                                1
                            ).upper()

                if not field_metadata:
                    continue

                field_metadata[
                    "projection_area"
                ] = projection_area

                field_metadata[
                    "query_ref"
                ] = projection.get(
                    "queryRef"
                )

                field_metadata[
                    "native_query_ref"
                ] = projection.get(
                    "nativeQueryRef"
                )

                if (
                    field_metadata.get("column_name")
                    in {
                        "Country",
                        "LegalEntityCode",
                    }
                ):

                    logger.warning(
                        "PBIR FIELD BEFORE RESULT: %s",
                        field_metadata,
                    )



                result.append(
                    field_metadata
                )

        return result


    def _extract_pbir_visual_fields(
        self,
        visual,
    ):

        result = []

        raw = visual.get(
            "raw",
            {}
        )

        visual_definition = raw.get(
            "visual",
            {}
        )

        if not isinstance(
            visual_definition,
            dict,
        ):
            return result

        query = visual_definition.get(
            "query",
            {}
        )

        if not isinstance(
            query,
            dict,
        ):
            return result

        query_state = query.get(
            "queryState",
            {}
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
                []
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
                    {}
                )

                field_metadata = self._parse_field(
                    field
                )

                if not field_metadata:
                    continue

                # -------------------------------------------------------------
                # Projection / role metadata
                # -------------------------------------------------------------

                field_metadata[
                    "projection_area"
                ] = projection_area

                # -------------------------------------------------------------
                # Query reference
                # -------------------------------------------------------------

                field_metadata[
                    "query_ref"
                ] = projection.get(
                    "queryRef"
                )

                # -------------------------------------------------------------
                # Native query reference
                # -------------------------------------------------------------

                field_metadata[
                    "native_query_ref"
                ] = projection.get(
                    "nativeQueryRef"
                )

                result.append(
                    field_metadata
                )

        return result


    def _extract_legacy_visual_fields(
        self,
        visual,
    ):

        result = []

        raw = visual.get(
            "raw",
            {}
        )

        if not isinstance(
            raw,
            dict,
        ):
            return result

        # -------------------------------------------------------------------------
        # Find the query that contains the visual's select items.
        #
        # For PBIR-Legacy visuals, the query may be stored in:
        #
        #   1. legacyQuery
        #   2. legacyConfig.singleVisual.prototypeQuery
        #   3. legacyConfig.singleVisual.query
        #
        # We keep the exact query candidate that produced the select_items.
        # This is important because the From -> Entity definitions must come
        # from the same query.
        # -------------------------------------------------------------------------

        query_candidates = []

        legacy_query = raw.get(
            "legacyQuery",
            {}
        )

        if isinstance(
            legacy_query,
            (dict, list, str),
        ):
            query_candidates.append(
                legacy_query
            )

        legacy_config = raw.get(
            "legacyConfig",
            {}
        )

        single_visual = {}

        if isinstance(
            legacy_config,
            dict,
        ):

            candidate_single_visual = (
                legacy_config.get(
                    "singleVisual",
                    {}
                )
            )

            if isinstance(
                candidate_single_visual,
                dict,
            ):
                single_visual = candidate_single_visual

        if isinstance(
            single_visual,
            dict,
        ):

            prototype_query = (
                single_visual.get(
                    "prototypeQuery"
                )
            )

            if isinstance(
                prototype_query,
                (dict, list, str),
            ):
                query_candidates.append(
                    prototype_query
                )

            visual_query = (
                single_visual.get(
                    "query"
                )
            )

            if isinstance(
                visual_query,
                (dict, list, str),
            ):
                query_candidates.append(
                    visual_query
                )

        # -------------------------------------------------------------------------
        # Find the first query candidate that actually contains select items.
        # -------------------------------------------------------------------------

        selected_query = None
        select_items = []

        for query_candidate in query_candidates:

            candidate_select_items = (
                self._extract_legacy_select_items(
                    query_candidate
                )
            )

            if candidate_select_items:

                selected_query = query_candidate

                select_items = (
                    candidate_select_items
                )

                break

        if not select_items:
            return result

        # -------------------------------------------------------------------------
        # Extract source aliases/entities from the SAME query that supplied
        # the select items.
        #
        # Example:
        #
        #   From:
        #   [
        #       {
        #           "Name": "d",
        #           "Entity": "dim_LegalEntity"
        #       }
        #   ]
        #
        # becomes:
        #
        #   {
        #       "d": "dim_LegalEntity"
        #   }
        # -------------------------------------------------------------------------

        source_entities = (
            self._extract_legacy_source_entities(
                selected_query
            )
        )

        # -------------------------------------------------------------------------
        # Build field metadata.
        # -------------------------------------------------------------------------

        for select_item in select_items:

            if not isinstance(
                select_item,
                dict,
            ):
                continue

            field = None

            # -------------------------------------------------------------
            # Normal Column
            # -------------------------------------------------------------

            if isinstance(
                select_item.get("Column"),
                dict,
            ):

                field = {
                    "Column": select_item.get(
                        "Column"
                    )
                }

            # -------------------------------------------------------------
            # Measure
            # -------------------------------------------------------------

            elif isinstance(
                select_item.get("Measure"),
                dict,
            ):

                field = {
                    "Measure": select_item.get(
                        "Measure"
                    )
                }

            # -------------------------------------------------------------
            # Aggregation
            # -------------------------------------------------------------

            elif isinstance(
                select_item.get("Aggregation"),
                dict,
            ):

                field = {
                    "Aggregation": select_item.get(
                        "Aggregation"
                    )
                }

            if field is None:
                continue

            # -------------------------------------------------------------
            # Parse the actual field.
            # -------------------------------------------------------------

            field_metadata = self._parse_field(
                field
            )

            if not field_metadata:
                continue

            # ---------------------------------------------------------------------
            # Legacy user-facing aggregation / summarization.
            #
            # IMPORTANT:
            #
            # For Legacy PBIR, the internal query can contain:
            #
            #   Function = 3
            #   Name = "Min(dim_LegalEntity.Country)"
            #
            # while the actual Power BI user selection is:
            #
            #   NativeReferenceName = "First Country"
            #
            # Therefore:
            #
            #   NativeReferenceName
            #           ↓
            #       FIRST
            #
            # takes precedence over the numeric Function value and the
            # internal Name.
            #
            # This is especially important for fields such as:
            #
            #   First Country
            #   First LegalEntityCode
            #
            # which Power BI may internally represent with Min(...).
            # ---------------------------------------------------------------------

            native_reference_name = (
                select_item.get(
                    "NativeReferenceName"
                )
            )

            if (
                field_metadata.get("field_type") == "Column"
                and isinstance(
                    native_reference_name,
                    str,
                )
            ):

                native_reference_name = (
                    native_reference_name.strip()
                )

                if native_reference_name:

                    aggregation_match = re.match(
                        r"^\s*"
                        r"(First|Last|Sum|Average|Min|Max|"
                        r"Count|CountRows|DistinctCount)"
                        r"\b",
                        native_reference_name,
                        re.IGNORECASE,
                    )

                    if aggregation_match:

                        field_metadata[
                            "aggregation_function"
                        ] = aggregation_match.group(
                            1
                        ).upper()

            # ---------------------------------------------------------------------
            # Resolve table name through the source alias when the field itself
            # does not contain the Entity directly.
            #
            # Column / Measure:
            #
            #   Expression
            #       SourceRef
            #           Source
            #
            # Aggregation:
            #
            #   Expression
            #       Column
            #           Expression
            #               SourceRef
            #                   Source
            # ---------------------------------------------------------------------

            if not field_metadata.get(
                "table_name"
            ):

                field_source = next(
                    iter(field.values()),
                    {}
                )

                source_alias = None

                if isinstance(
                    field_source,
                    dict,
                ):

                    # -------------------------------------------------------------
                    # Normal Column / Measure
                    # -------------------------------------------------------------

                    expression = field_source.get(
                        "Expression",
                        {}
                    )

                    if isinstance(
                        expression,
                        dict,
                    ):

                        source_ref = expression.get(
                            "SourceRef",
                            {}
                        )

                        if isinstance(
                            source_ref,
                            dict,
                        ):

                            source_alias = (
                                source_ref.get(
                                    "Source"
                                )
                            )

                    # -------------------------------------------------------------
                    # Aggregation
                    # -------------------------------------------------------------

                    if (
                        source_alias is None
                        and "Aggregation" in field
                    ):

                        aggregation_expression = (
                            field_source.get(
                                "Expression",
                                {}
                            )
                        )

                        if isinstance(
                            aggregation_expression,
                            dict,
                        ):

                            column = (
                                aggregation_expression.get(
                                    "Column",
                                    {}
                                )
                            )

                            if isinstance(
                                column,
                                dict,
                            ):

                                column_expression = (
                                    column.get(
                                        "Expression",
                                        {}
                                    )
                                )

                                if isinstance(
                                    column_expression,
                                    dict,
                                ):

                                    source_ref = (
                                        column_expression.get(
                                            "SourceRef",
                                            {}
                                        )
                                    )

                                    if isinstance(
                                        source_ref,
                                        dict,
                                    ):

                                        source_alias = (
                                            source_ref.get(
                                                "Source"
                                            )
                                        )

                if source_alias:

                    field_metadata[
                        "table_name"
                    ] = source_entities.get(
                        str(source_alias),
                        source_alias,
                    )

            # ---------------------------------------------------------------------
            # Projection / role metadata.
            # ---------------------------------------------------------------------

            field_metadata[
                "projection_area"
            ] = (
                select_item.get(
                    "ProjectionArea"
                )
                or select_item.get(
                    "Role"
                )
                or "Select"
            )

            # ---------------------------------------------------------------------
            # Query reference.
            #
            # Keep the internal Legacy query representation here.
            # Example:
            #
            #   Min(dim_LegalEntity.Country)
            # ---------------------------------------------------------------------

            field_metadata[
                "query_ref"
            ] = (
                select_item.get(
                    "Name"
                )
                or select_item.get(
                    "QueryRef"
                )
                or select_item.get(
                    "queryRef"
                )
            )

            # ---------------------------------------------------------------------
            # Native query reference.
            #
            # This preserves the user-facing Power BI field/summarization name.
            #
            # Example:
            #
            #   First Country
            # ---------------------------------------------------------------------

            field_metadata[
                "native_query_ref"
            ] = (
                select_item.get(
                    "NativeReferenceName"
                )
                or select_item.get(
                    "NativeQueryRef"
                )
                or select_item.get(
                    "nativeQueryRef"
                )
            )

            result.append(
                field_metadata
            )

        return result



    def _extract_legacy_source_entities(
        self,
        query,
    ):

        query = self._parse_json_value(
            query
        )

        source_entities = {}

        def walk(value):

            value = self._parse_json_value(
                value
            )

            if isinstance(
                value,
                dict,
            ):

                sources = value.get(
                    "From"
                )

                if isinstance(
                    sources,
                    list,
                ):

                    for source in sources:

                        if not isinstance(
                            source,
                            dict,
                        ):

                            continue

                        alias = source.get(
                            "Name"
                        )

                        entity = source.get(
                            "Entity"
                        )

                        if alias and entity:

                            source_entities[
                                str(alias)
                            ] = str(entity)

                for child in value.values():

                    walk(child)

            elif isinstance(
                value,
                list,
            ):

                for child in value:

                    walk(child)

        walk(query)

        return source_entities

    def _extract_legacy_select_items(
        self,
        query,
    ):

        query = self._parse_json_value(
            query
        )

        if not isinstance(
            query,
            (
                dict,
                list,
            ),
        ):

            return []

        if isinstance(
            query,
            dict,
        ):

            direct_query = query.get(
                "Query"
            )

            if isinstance(
                direct_query,
                dict,
            ):

                select_items = direct_query.get(
                    "Select"
                )

                if isinstance(
                    select_items,
                    list,
                ):

                    return select_items

            commands = query.get(
                "Commands"
            )

            if isinstance(
                commands,
                list,
            ):

                for command in commands:

                    if not isinstance(
                        command,
                        dict,
                    ):

                        continue

                    semantic_command = command.get(
                        "SemanticQueryDataShapeCommand"
                    )

                    if not isinstance(
                        semantic_command,
                        dict,
                    ):

                        continue

                    command_query = (
                        semantic_command.get(
                            "Query"
                        )
                    )

                    if not isinstance(
                        command_query,
                        dict,
                    ):

                        continue

                    select_items = (
                        command_query.get(
                            "Select"
                        )
                    )

                    if isinstance(
                        select_items,
                        list,
                    ):

                        return select_items

        found = []

        def walk(value):

            value = self._parse_json_value(
                value
            )

            if isinstance(
                value,
                dict,
            ):

                select = value.get(
                    "Select"
                )

                if isinstance(
                    select,
                    list,
                ):

                    found.extend(
                        select
                    )

                    return True

                for child in value.values():

                    if walk(child):
                        return True

            elif isinstance(
                value,
                list,
            ):

                for child in value:

                    if walk(child):
                        return True

            return False

        walk(query)

        return found

    # ========================================================================
    # FIELD PARSER
    # ========================================================================

    def _parse_field(
        self,
        field,
    ):

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
                {}
            )

            if not isinstance(
                measure,
                dict,
            ):

                return None

            expression = measure.get(
                "Expression",
                {}
            )

            if not isinstance(
                expression,
                dict,
            ):

                expression = {}

            source_ref = expression.get(
                "SourceRef",
                {}
            )

            if not isinstance(
                source_ref,
                dict,
            ):

                source_ref = {}

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
                {}
            )

            if not isinstance(
                column,
                dict,
            ):

                return None

            expression = column.get(
                "Expression",
                {}
            )

            if not isinstance(
                expression,
                dict,
            ):

                expression = {}

            source_ref = expression.get(
                "SourceRef",
                {}
            )

            if not isinstance(
                source_ref,
                dict,
            ):

                source_ref = {}

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
                {}
            )

            if not isinstance(
                aggregation,
                dict,
            ):

                return None

            expression = aggregation.get(
                "Expression",
                {}
            )

            if not isinstance(
                expression,
                dict,
            ):

                expression = {}

            function_code = aggregation.get(
                "Function"
            )

            column = expression.get(
                "Column",
                {}
            )

            if not isinstance(
                column,
                dict,
            ):

                column = {}

            column_expression = column.get(
                "Expression",
                {}
            )

            if not isinstance(
                column_expression,
                dict,
            ):

                column_expression = {}

            source_ref = column_expression.get(
                "SourceRef",
                {}
            )

            if not isinstance(
                source_ref,
                dict,
            ):

                source_ref = {}

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
                "aggregation_function": self._aggregation_name(
                    function_code
                ),
            }

        # Date hierarchy fields retain the underlying column in a
        # PropertyVariationSource instead of a regular Column node.
        if "HierarchyLevel" in field:

            hierarchy_level = field.get(
                "HierarchyLevel",
                {},
            )

            if not isinstance(
                hierarchy_level,
                dict,
            ):

                return None

            expression = hierarchy_level.get(
                "Expression",
                {},
            )

            hierarchy = (
                expression.get(
                    "Hierarchy",
                    {},
                )
                if isinstance(expression, dict)
                else {}
            )

            hierarchy_expression = (
                hierarchy.get(
                    "Expression",
                    {},
                )
                if isinstance(hierarchy, dict)
                else {}
            )

            variation_source = (
                hierarchy_expression.get(
                    "PropertyVariationSource",
                    {},
                )
                if isinstance(hierarchy_expression, dict)
                else {}
            )

            variation_expression = (
                variation_source.get(
                    "Expression",
                    {},
                )
                if isinstance(variation_source, dict)
                else {}
            )

            source_ref = (
                variation_expression.get(
                    "SourceRef",
                    {},
                )
                if isinstance(variation_expression, dict)
                else {}
            )

            table_name = (
                source_ref.get(
                    "Entity"
                )
                if isinstance(source_ref, dict)
                else None
            )

            column_name = (
                variation_source.get(
                    "Property"
                )
                if isinstance(variation_source, dict)
                else None
            )

            if table_name and column_name:

                return {
                    "field_type": "Column",
                    "table_name": table_name,
                    "column_name": column_name,
                    "measure_name": None,
                    "aggregation_function": None,
                }

        return None

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
    #
    # IMPORTANT:
    #
    # A field being present in a visual does NOT mean it is a filter.
    #
    # We therefore require evidence that the filter definition actually
    # represents a persisted filter:
    #
    #   1. explicit filter expression / Where
    #   2. actual filter payload
    #   3. meaningful howCreated metadata
    #
    # This preserves TopN / Advanced filters while eliminating the
    # field-only entries that previously appeared as filters.
    # ========================================================================

    def extract_visual_filters(
        self,
        visual,
    ):

        if self.report_format == "PBIR-Legacy":

            return self._extract_legacy_visual_filters(
                visual
            )

        return self._extract_pbir_visual_filters(
            visual
        )

    # ========================================================================
    # FILTER FIELD PARSER
    # ========================================================================

    def _parse_filter_field(
        self,
        field,
    ):
        """
        Parse the field/target associated with a filter.

        This is deliberately an INSTANCE method.

        Previous broken version called:

            cls._parse_filter_field(field)

        while the method required:

            self, field

        That caused:

            TypeError:
            missing 1 required positional argument: 'field'
        """

        if not isinstance(
            field,
            dict,
        ):

            return None

        # Normal PBIR field structure.
        parsed = self._parse_field(
            field
        )

        if parsed:
            return parsed

        # Some filter definitions can expose the target in slightly
        # different structures. Try common wrappers defensively.

        for key in (
            "field",
            "target",
            "expression",
            "Field",
            "Target",
            "Expression",
        ):

            nested = field.get(
                key
            )

            if not isinstance(
                nested,
                dict,
            ):

                continue

            parsed = self._parse_field(
                nested
            )

            if parsed:
                return parsed

        return None

    # ========================================================================
    # FILTER STRUCTURE HELPERS
    # ========================================================================

    def _contains_where(
        self,
        value,
    ):

        if isinstance(
            value,
            dict,
        ):

            if "Where" in value:
                return True

            for child in value.values():

                if self._contains_where(
                    child
                ):

                    return True

        elif isinstance(
            value,
            list,
        ):

            for child in value:

                if self._contains_where(
                    child
                ):

                    return True

        return False

    def _contains_filter_expression(
        self,
        value,
    ):
        """
        Detect an actual persisted filter expression.

        We intentionally do not treat merely having a `field` as a filter.
        """

        if not isinstance(
            value,
            (
                dict,
                list,
            ),
        ):

            return False

        if self._contains_where(
            value
        ):

            return True

        if isinstance(
            value,
            dict,
        ):

            # Common filter-expression containers.
            expression_keys = {
                "condition",
                "conditions",
                "operator",
                "comparison",
                "comparisons",
                "value",
                "values",
                "valuesMetadata",
                "filter",
                "Filter",
                "filterExpression",
                "expression",
                "Expression",
            }

            for key in expression_keys:

                if key not in value:
                    continue

                child = value.get(
                    key
                )

                if isinstance(
                    child,
                    (
                        dict,
                        list,
                    ),
                ):

                    if self._contains_filter_expression(
                        child
                    ):

                        return True

                elif child is not None:

                    return True

        elif isinstance(
            value,
            list,
        ):

            for child in value:

                if self._contains_filter_expression(
                    child
                ):

                    return True

        return False

    def _has_filter_target(
        self,
        filter_definition,
    ):
        """
        Determine whether a filter has a real field/target.

        IMPORTANT:
        This method uses `self._parse_filter_field()`, not
        `cls._parse_filter_field()`.
        """

        if not isinstance(
            filter_definition,
            dict,
        ):

            return False

        field = filter_definition.get(
            "field"
        )

        if isinstance(
            field,
            dict,
        ):

            if self._parse_filter_field(
                field
            ):

                return True

        target = filter_definition.get(
            "target"
        )

        if isinstance(
            target,
            dict,
        ):

            if any(
                target.get(key)
                for key in (
                    "table",
                    "entity",
                    "Entity",
                    "column",
                    "property",
                    "Column",
                    "measure",
                    "Measure",
                )
            ):

                return True

        # Some definitions put the field under the filter object.
        nested_filter = filter_definition.get(
            "filter"
        )

        if isinstance(
            nested_filter,
            dict,
        ):

            nested_field = nested_filter.get(
                "field"
            )

            if isinstance(
                nested_field,
                dict,
            ):

                if self._parse_filter_field(
                    nested_field
                ):

                    return True

        return False

    def _is_actual_filter(
        self,
        filter_definition,
    ):
        """
        Decide whether a filterConfig entry represents an ACTUAL filter.

        This is the central distinction between:

            visual field/projection
                         VS
            persisted filter

        Rules:

        A) A filter with a real filter expression is accepted.

        B) TopN with a real filter payload is accepted.

        C) Advanced with a real filter payload is accepted.

        D) Explicit howCreated metadata plus a real target is accepted.

        E) A field-only definition without filter criteria is rejected.
        """

        if not isinstance(
            filter_definition,
            dict,
        ):

            return False

        filter_type = (
            filter_definition.get(
                "type"
            )
            or filter_definition.get(
                "filterType"
            )
            or ""
        )

        filter_type_normalized = str(
            filter_type
        ).strip().lower()

        nested_filter = filter_definition.get(
            "filter"
        )

        how_created = filter_definition.get(
            "howCreated"
        )

        # ---------------------------------------------------------------
        # 1. Actual filter payload
        # ---------------------------------------------------------------

        if isinstance(
            nested_filter,
            (
                dict,
                list,
            ),
        ):

            if self._contains_filter_expression(
                nested_filter
            ):

                return True

        # ---------------------------------------------------------------
        # 2. Filter expression elsewhere in definition
        # ---------------------------------------------------------------

        if self._contains_filter_expression(
            filter_definition
        ):

            # Make sure this is not simply a visual field definition.
            if (
                self._has_filter_target(
                    filter_definition
                )
                or filter_type_normalized in {
                    "topn",
                    "advanced",
                    "categorical",
                    "relativeDate".lower(),
                    "relativedate",
                    "relativetime",
                    "include",
                    "exclude",
                    "range",
                }
            ):

                return True

        # ---------------------------------------------------------------
        # 3. TopN
        #
        # TopN is always a genuine filter when its definition contains
        # an actual filter payload.
        # ---------------------------------------------------------------

        if filter_type_normalized == "topn":

            if isinstance(
                nested_filter,
                (
                    dict,
                    list,
                ),
            ):

                return True

            if how_created:

                return self._has_filter_target(
                    filter_definition
                )

        # ---------------------------------------------------------------
        # 4. Advanced
        # ---------------------------------------------------------------

        if filter_type_normalized == "advanced":

            if isinstance(
                nested_filter,
                (
                    dict,
                    list,
                ),
            ):

                return True

            if how_created:

                return self._has_filter_target(
                    filter_definition
                )

        # ---------------------------------------------------------------
        # 5. Explicit creation metadata
        #
        # Only accept this if the object ALSO has a real target.
        # This prevents a bare visual field from becoming a filter.
        # ---------------------------------------------------------------

        if how_created is not None:

            how_created_normalized = str(
                how_created
            ).strip().lower()

            meaningful_creation_values = {
                "user",
                "include",
                "exclude",
                "drillthrough",
                "copilot",
                "generated",
                "explicit",
            }

            if (
                how_created_normalized
                in meaningful_creation_values
                and self._has_filter_target(
                    filter_definition
                )
            ):

                # If it has a filter payload, definitely accept.
                if isinstance(
                    nested_filter,
                    (
                        dict,
                        list,
                    ),
                ):

                    return True

                # If there is no payload, only accept for explicit
                # filter types where the metadata itself is sufficient.
                if filter_type_normalized in {
                    "include",
                    "exclude",
                    "topn",
                    "advanced",
                    "relativedate",
                    "relativetime",
                    "range",
                }:

                    return True

        # ---------------------------------------------------------------
        # 6. Otherwise this is most likely an automatic field/projection
        #    entry and must NOT be stored as a filter.
        # ---------------------------------------------------------------

        return False

    # ========================================================================
    # FILTER FIELD EXTRACTION
    # ========================================================================

    def _extract_filter_field_metadata(
        self,
        filter_definition,
    ):

        field_candidates = []

        field = filter_definition.get(
            "field"
        )

        if isinstance(
            field,
            dict,
        ):

            field_candidates.append(
                field
            )

        target = filter_definition.get(
            "target"
        )

        if isinstance(
            target,
            dict,
        ):

            field_candidates.append(
                target
            )

        nested_filter = filter_definition.get(
            "filter"
        )

        expression = filter_definition.get(
            "expression"
        )

        if isinstance(
            expression,
            dict,
        ):

            field_candidates.append(
                expression
            )

        if isinstance(
            nested_filter,
            dict,
        ):

            nested_field = nested_filter.get(
                "field"
            )

            if isinstance(
                nested_field,
                dict,
            ):

                field_candidates.append(
                    nested_field
                )

        for candidate in field_candidates:

            metadata = self._parse_filter_field(
                candidate
            )

            if metadata:
                return metadata

        # ---------------------------------------------------------------
        # Legacy target fallback
        # ---------------------------------------------------------------

        target = filter_definition.get(
            "target",
            {}
        )

        if not isinstance(
            target,
            dict,
        ):

            target = {}

        table_name = (
            target.get("table")
            or target.get("entity")
            or target.get("Entity")
        )

        column_name = (
            target.get("column")
            or target.get("property")
            or target.get("Column")
        )

        measure_name = (
            target.get("measure")
            or target.get("Measure")
        )

        if measure_name:

            return {
                "field_type": "Measure",
                "table_name": table_name,
                "column_name": None,
                "measure_name": measure_name,
                "aggregation_function": None,
            }

        if column_name:

            return {
                "field_type": "Column",
                "table_name": table_name,
                "column_name": column_name,
                "measure_name": None,
                "aggregation_function": None,
            }

        return None

    # ========================================================================
    # PBIR VISUAL FILTERS
    # ========================================================================


    def _extract_pbir_visual_filters(
        self,
        visual,
    ):

        result = []

        raw = visual.get(
            "raw",
            {}
        )

        filter_config = raw.get(
            "filterConfig",
            {}
        )

        if not isinstance(
            filter_config,
            dict,
        ):

            return result

        filters = filter_config.get(
            "filters",
            []
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

            if not self._is_actual_filter(
                filter_definition
            ):

                continue

            field_metadata = (
                self._extract_filter_field_metadata(
                    filter_definition
                )
            )

            result.append(
                {
                    "filter_name": (
                        filter_definition.get(
                            "name"
                        )
                    ),
                    "field_type": (
                        field_metadata.get(
                            "field_type"
                        )
                        if field_metadata
                        else None
                    ),
                    "table_name": (
                        field_metadata.get(
                            "table_name"
                        )
                        if field_metadata
                        else None
                    ),
                    "column_name": (
                        field_metadata.get(
                            "column_name"
                        )
                        if field_metadata
                        else None
                    ),
                    "measure_name": (
                        field_metadata.get(
                            "measure_name"
                        )
                        if field_metadata
                        else None
                    ),
                    "filter_type": (
                        filter_definition.get(
                            "type"
                        )
                        or filter_definition.get(
                            "filterType"
                        )
                    ),
                }
            )

        return result



    # ========================================================================
    # LEGACY VISUAL FILTERS
    # ========================================================================

    def _extract_legacy_visual_filters(
        self,
        visual,
    ):

        result = []

        raw = visual.get(
            "raw",
            {}
        )

        filters = raw.get(
            "legacyFilters",
            []
        )

        if isinstance(
            filters,
            dict,
        ):

            filters = [
                filters
            ]

        if not isinstance(
            filters,
            list,
        ):

            return result

        for index, filter_definition in enumerate(
            filters,
            start=1,
        ):

            if not isinstance(
                filter_definition,
                dict,
            ):

                continue

            if not self._is_actual_filter(
                filter_definition
            ):

                target = filter_definition.get(
                    "target",
                    {},
                )

                if not isinstance(
                    target,
                    dict,
                ):

                    target = {}

                has_target = any(
                    target.get(key)
                    for key in (
                        "table",
                        "entity",
                        "Entity",
                        "column",
                        "property",
                        "Column",
                        "measure",
                        "Measure",
                    )
                )

                has_value = any(
                    filter_definition.get(key) is not None
                    for key in (
                        "value",
                        "values",
                        "conditions",
                        "condition",
                    )
                )

                if not (
                    has_target
                    and has_value
                ):

                    continue

            target = filter_definition.get(
                "target",
                {}
            )

            if not isinstance(
                target,
                dict,
            ):

                target = {}

            table_name = (
                target.get("table")
                or target.get("entity")
                or target.get("Entity")
            )

            column_name = (
                target.get("column")
                or target.get("property")
                or target.get("Column")
            )

            measure_name = (
                target.get("measure")
                or target.get("Measure")
            )

            field_metadata = (
                self._extract_filter_field_metadata(
                    filter_definition
                )
            )

            if field_metadata:

                table_name = (
                    table_name
                    or field_metadata.get(
                        "table_name"
                    )
                )

                column_name = (
                    column_name
                    or field_metadata.get(
                        "column_name"
                    )
                )

                measure_name = (
                    measure_name
                    or field_metadata.get(
                        "measure_name"
                    )
                )

            if measure_name:

                field_type = "Measure"

            elif column_name:

                field_type = "Column"

            else:

                field_type = None

            result.append(
                {
                    "filter_name": (
                        filter_definition.get(
                            "name"
                        )
                        or filter_definition.get(
                            "displayName"
                        )
                        or f"LegacyFilter_{index}"
                    ),
                    "field_type": field_type,
                    "table_name": table_name,
                    "column_name": column_name,
                    "measure_name": measure_name,
                    "filter_type": (
                        filter_definition.get(
                            "type"
                        )
                        or filter_definition.get(
                            "filterType"
                        )
                        or filter_definition.get(
                            "condition"
                        )
                    ),
                }
            )

        return result


# ============================================================================
# LOOKUPS
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

    for row in cursor.fetchall():

        lookup[
            (
                int(row[1]),
                str(row[2]),
            )
        ] = int(row[0])

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

    for row in cursor.fetchall():

        lookup[
            (
                int(row[2]),
                str(row[3]),
                str(row[4]),
            )
        ] = int(row[0])

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

    for row in cursor.fetchall():

        lookup[
            (
                int(row[1]),
                str(row[2]),
            )
        ] = int(row[0])

    return lookup


# ============================================================================
# REPORT REPOSITORY
# ============================================================================

def get_or_create_report(
    cursor,
    report_name,
    report_id,
    semantic_model_id,
    workspace_id,
    workspace_name,
):

    cursor.execute(
        """
        SELECT ReportID
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
            workspace_id,
            workspace_name,
            semantic_model_id,
            "Microsoft Fabric Report",
            repository_report_id,
        )

        logger.info(
            "Updated MetadataReport %s for workspace %s",
            repository_report_id,
            workspace_name,
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
        workspace_id,
        workspace_name,
        report_id,
        semantic_model_id,
        "Microsoft Fabric Report",
    )

    cursor.execute(
        """
        SELECT ReportID
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

    return int(
        result[0]
    )


# ============================================================================
# CLEAR REPORT CHILDREN
# ============================================================================

def clear_report_children(
    cursor,
    report_id,
):

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


# ============================================================================
# CLEAR REPORT LINEAGE
# ============================================================================

def clear_report_lineage(
    cursor,
    report_id,
):

    cursor.execute(
        """
        DELETE FROM dbo.MetadataLineage
        WHERE FromEntityType = 'REPORT'
          AND FromEntityID = ?
        """,
        report_id,
    )

    cursor.execute(
        """
        DELETE FROM dbo.MetadataLineage
        WHERE FromEntityType = 'REPORT'
          AND LineageType = 'REPORT_TO_PAGE'
          AND FromEntityID = ?
        """,
        report_id,
    )

    cursor.execute(
        """
        DELETE FROM dbo.MetadataLineage
        WHERE FromEntityType = 'PAGE'
          AND ToEntityType = 'VISUAL'
          AND FromEntityID IN
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
        DELETE FROM dbo.MetadataLineage
        WHERE FromEntityType = 'VISUAL'
          AND ToEntityType IN
        (
            'SEMANTIC_COLUMN',
            'MEASURE'
        )
          AND FromEntityID IN
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

    logger.info(
        "Cleared report lineage for ReportID=%s",
        report_id,
    )


def reconcile_deleted_reports(
    cursor,
    workspace_id,
    discovered_report_ids,
):

    cursor.execute(
        """
        SELECT ReportID, FabricReportID
        FROM dbo.MetadataReport
        WHERE WorkspaceID = ?
        """,
        workspace_id,
    )

    discovered_ids = {
        str(report_id).lower()
        for report_id in discovered_report_ids
    }

    deleted_count = 0

    existing_reports = cursor.fetchall()

    for repository_report_id, fabric_report_id in existing_reports:

        if str(fabric_report_id).lower() in discovered_ids:
            continue

        clear_report_lineage(
            cursor,
            int(repository_report_id),
        )

        clear_report_children(
            cursor,
            int(repository_report_id),
        )

        cursor.execute(
            """
            DELETE FROM dbo.MetadataReport
            WHERE ReportID = ?
            """,
            int(repository_report_id),
        )

        deleted_count += 1

        logger.info(
            "Removed deleted Fabric report %s from workspace %s.",
            fabric_report_id,
            workspace_id,
        )

    return deleted_count


# ============================================================================
# LOAD PAGES
# ============================================================================

def load_pages(
    cursor,
    report_id,
    pages,
):

    if not pages:
        return {}

    execute_insert_batches(
        cursor,
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
        [
            (
                report_id,
                page["page_name"],
                page["display_name"],
                page["page_order"],
            )
            for page in pages
        ],
    )

    cursor.execute(
        """
        SELECT PageID, PageName
        FROM dbo.MetadataReportPage
        WHERE ReportID = ?
        """,
        report_id,
    )

    lookup = {
        str(page_name): int(page_id)
        for page_id, page_name in cursor.fetchall()
    }

    return lookup


# ============================================================================
# LOAD VISUALS
# ============================================================================

def load_visuals(
    cursor,
    report_id,
    page_lookup,
    visuals,
):

    if not visuals:
        return {}

    rows = []

    for visual in visuals:

        page_id = page_lookup.get(
            visual["page_name"]
        )

        if page_id is None:

            logger.warning(
                "Page not found for visual %s",
                visual["fabric_visual_id"],
            )

            continue

        rows.append(
            (
                page_id,
                visual["fabric_visual_id"],
                visual["visual_type"],
            )
        )

    if not rows:
        return {}

    execute_insert_batches(
        cursor,
        """
        INSERT INTO dbo.MetadataReportVisual
        (
            PageID,
            FabricVisualID,
            VisualType
        )
        VALUES (?, ?, ?)
        """,
        rows,
    )

    cursor.execute(
        """
        SELECT
            v.VisualID,
            v.FabricVisualID
        FROM dbo.MetadataReportVisual v
        INNER JOIN dbo.MetadataReportPage p
            ON v.PageID = p.PageID
        WHERE p.ReportID = ?
        """,
        report_id,
    )

    lookup = {
        str(fabric_visual_id): int(visual_id)
        for visual_id, fabric_visual_id in cursor.fetchall()
    }

    return lookup


# ============================================================================
# FIELD ID RESOLUTION
# ============================================================================

def resolve_semantic_table_name(
    semantic_model_id,
    table_name,
    semantic_table_lookup,
):
    """
    Resolve a PBIR table/entity name to the canonical semantic-model
    table name.

    Resolution order:
        1. Exact table-name match.
        2. PBIR dimension alias (dim_X -> X), but only when the
           candidate table actually exists in the current semantic model.

    Returns:
        Canonical semantic table name, or None if unresolved.
    """

    if not table_name:
        return None

    table_name = str(
        table_name
    ).strip()

    if not table_name:
        return None

    # ------------------------------------------------------------------
    # 1. Exact semantic-model table name
    # ------------------------------------------------------------------

    if (
        semantic_model_id,
        table_name,
    ) in semantic_table_lookup:

        return table_name

    # ------------------------------------------------------------------
    # 2. PBIR dimension alias
    #
    # Example:
    #     PBIR:     dim_Date
    #     Semantic: Date
    #
    # Only accept the transformation if the candidate table actually
    # exists in this semantic model.
    # ------------------------------------------------------------------

    if table_name.lower().startswith(
        "dim_"
    ):

        candidate_table_name = table_name[
            4:
        ].strip()

        if candidate_table_name:

            if (
                semantic_model_id,
                candidate_table_name,
            ) in semantic_table_lookup:

                return candidate_table_name

    # ------------------------------------------------------------------
    # 3. No safe resolution
    # ------------------------------------------------------------------

    return None


def resolve_field_ids(
    field,
    semantic_model_id,
    semantic_table_lookup,
    semantic_column_lookup,
    measure_lookup,
):

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

    # ------------------------------------------------------------------
    # Resolve table name
    # ------------------------------------------------------------------

    resolved_table_name = (
        resolve_semantic_table_name(
            semantic_model_id,
            table_name,
            semantic_table_lookup,
        )
    )

    if resolved_table_name:

        semantic_table_id = (
            semantic_table_lookup.get(
                (
                    semantic_model_id,
                    resolved_table_name,
                )
            )
        )

    # ------------------------------------------------------------------
    # Resolve column using the RESOLVED semantic table name.
    #
    # This is important:
    #
    #     PBIR: dim_Date[Date]
    #
    # becomes:
    #
    #     Semantic: Date[Date]
    #
    # We never search for a column globally by column name.
    # ------------------------------------------------------------------

    if (
        resolved_table_name
        and column_name
    ):

        semantic_column_id = (
            semantic_column_lookup.get(
                (
                    semantic_model_id,
                    resolved_table_name,
                    str(column_name),
                )
            )
        )

    # ------------------------------------------------------------------
    # Resolve measure
    # ------------------------------------------------------------------

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

    rows = []
    unresolved = 0

    for visual in visuals:

        visual_id = visual_lookup.get(
            visual["fabric_visual_id"]
        )

        if visual_id is None:
            continue

        for field in visual.get(
            "fields",
            [],
        ):

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

                logger.warning(
                    "Unresolved semantic table: %s",
                    field.get("table_name"),
                )

            if (
                field.get("column_name")
                and semantic_column_id is None
            ):

                unresolved += 1

                logger.warning(
                    "Unresolved semantic column: %s.%s",
                    field.get("table_name"),
                    field.get("column_name"),
                )

            if (
                field.get("measure_name")
                and measure_id is None
            ):

                unresolved += 1

                logger.warning(
                    "Unresolved measure: %s",
                    field.get("measure_name"),
                )

            rows.append(
                (
                    visual_id,
                    field.get("field_type"),
                    semantic_table_id,
                    semantic_column_id,
                    measure_id,
                    field.get("aggregation_function"),
                    field.get("projection_area"),
                    field.get("query_ref"),
                    field.get("native_query_ref"),
                )
            )

    if rows:

        execute_insert_batches(
            cursor,
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
            rows,
        )

    if unresolved:

        logger.warning(
            "Unresolved visual field references: %d",
            unresolved,
        )

    return len(rows)


# ============================================================================
# LOAD FILTERS
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

    rows = []
    unresolved = 0
    for visual in visuals:

        visual_id = visual_lookup.get(
            visual["fabric_visual_id"]
        )

        if visual_id is None:
            continue

        for filter_definition in visual.get(
            "filters",
            [],
        ):

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

            rows.append(
                (
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
            )

    if rows:

        execute_insert_batches(
            cursor,
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
            rows,
        )

    if unresolved:

        logger.warning(
            "Unresolved visual filter references: %d",
            unresolved,
        )

    return len(rows)


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

        total_fields += len(fields)
        total_filters += len(filters)

    return (
        total_fields,
        total_filters,
    )


# ============================================================================
# REPORT LINEAGE
# ============================================================================

def load_report_lineage(
    cursor,
    report_id,
    repository_semantic_model_id,
    page_lookup,
    visual_lookup,
    visuals,
):

    rows = []

    if repository_semantic_model_id is not None:

        rows.append(
            (
                "REPORT",
                int(report_id),
                "SEMANTIC_MODEL",
                int(repository_semantic_model_id),
                "REPORT_TO_SEMANTIC_MODEL",
                None,
                "REPORT_SEMANTIC_MODEL_REFERENCE",
            )
        )

    for page_name, page_id in page_lookup.items():

        if page_id is None:
            continue

        rows.append(
            (
                "REPORT",
                int(report_id),
                "PAGE",
                int(page_id),
                "REPORT_TO_PAGE",
                None,
                None,
            )
        )

    for visual in visuals:

        page_id = page_lookup.get(
            visual["page_name"]
        )

        visual_id = visual_lookup.get(
            visual["fabric_visual_id"]
        )

        if page_id is None or visual_id is None:
            continue

        rows.append(
            (
                "PAGE",
                int(page_id),
                "VISUAL",
                int(visual_id),
                "PAGE_TO_VISUAL",
                visual.get(
                    "definition_path"
                ),
                "REPORT_DEFINITION",
            )
        )

    for visual in visuals:

        visual_id = visual_lookup.get(
            visual["fabric_visual_id"]
        )

        if visual_id is None:
            continue

        for field in visual.get(
            "fields",
            [],
        ):

            semantic_column_id = field.get(
                "_resolved_semantic_column_id"
            )

            measure_id = field.get(
                "_resolved_measure_id"
            )

            expression = (
                field.get("query_ref")
                or field.get("native_query_ref")
            )

            if semantic_column_id is not None:

                rows.append(
                    (
                        "VISUAL",
                        int(visual_id),
                        "SEMANTIC_COLUMN",
                        int(semantic_column_id),
                        "VISUAL_TO_SEMANTIC_COLUMN",
                        expression,
                        "REPORT_DEFINITION",
                    )
                )

            if measure_id is not None:

                rows.append(
                    (
                        "VISUAL",
                        int(visual_id),
                        "MEASURE",
                        int(measure_id),
                        "VISUAL_TO_MEASURE",
                        expression,
                        "REPORT_DEFINITION",
                    )
                )

    if rows:

        execute_insert_batches(
            cursor,
            """
            INSERT INTO dbo.MetadataLineage
            (
                FromEntityType,
                FromEntityID,
                ToEntityType,
                ToEntityID,
                LineageType,
                Expression,
                ResolutionMethod
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    logger.info(
        "Report lineage records inserted: %d",
        len(rows),
    )

    return len(rows)


# ============================================================================
# RESOLVE REPORT FIELD REFERENCES
# ============================================================================

def resolve_report_field_references(
    visuals,
    semantic_model_id,
    semantic_table_lookup,
    semantic_column_lookup,
    measure_lookup,
):

    for visual in visuals:

        for field in visual.get(
            "fields",
            [],
        ):

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

            field[
                "_resolved_semantic_table_id"
            ] = semantic_table_id

            field[
                "_resolved_semantic_column_id"
            ] = semantic_column_id

            field[
                "_resolved_measure_id"
            ] = measure_id


# ============================================================================
# PROCESS ONE REPORT
# ============================================================================

def process_report(
    client,
    cursor,
    connection,
    report,
    workspace_id,
    workspace_name,
    workspace_items,
    repository_models_cache,
    semantic_table_lookup,
    semantic_column_lookup,
    measure_lookup,
):

    report_name = report["name"]
    report_id = report["id"]

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
    # 1. GET DEFINITION
    # ========================================================================

    print()
    print(
        "Retrieving report definition..."
    )

    definition = client.get_report_definition(
        workspace_id,
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
            workspace_id,
            report,
            definition,
            cursor,
            workspace_items,
            repository_models_cache,
        )
    )

    print(
        "Fabric Semantic Model ID: "
        f"{fabric_semantic_model_id}"
    )

    # ========================================================================
    # 3. REPOSITORY MODEL
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
    # 4. PARSE
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

    # ========================================================================
    # 5. RESOLVE REPORT FIELD REFERENCES
    # ========================================================================

    resolve_report_field_references(
        visuals,
        repository_semantic_model_id,
        semantic_table_lookup,
        semantic_column_lookup,
        measure_lookup,
    )

    # ========================================================================
    # 6. DATABASE TRANSACTION
    # ========================================================================

    try:

        repository_report_id = (
            get_or_create_report(
                cursor,
                report_name,
                report_id,
                repository_semantic_model_id,
                workspace_id,
                workspace_name,
            )
        )

        print(
            "Repository ReportID: "
            f"{repository_report_id}"
        )

        clear_report_lineage(
            cursor,
            repository_report_id,
        )

        clear_report_children(
            cursor,
            repository_report_id,
        )

        page_lookup = load_pages(
            cursor,
            repository_report_id,
            pages,
        )

        visual_lookup = load_visuals(
            cursor,
            repository_report_id,
            page_lookup,
            visuals,
        )

        loaded_fields = load_visual_fields(
            cursor,
            visual_lookup,
            visuals,
            repository_semantic_model_id,
            semantic_table_lookup,
            semantic_column_lookup,
            measure_lookup,
        )

        loaded_filters = load_visual_filters(
            cursor,
            visual_lookup,
            visuals,
            repository_semantic_model_id,
            semantic_table_lookup,
            semantic_column_lookup,
            measure_lookup,
        )

        lineage_records = load_report_lineage(
            cursor,
            repository_report_id,
            repository_semantic_model_id,
            page_lookup,
            visual_lookup,
            visuals,
        )

        connection.commit()

    except Exception:

        rollback_transaction(
            connection,
            report_name,
        )

        logger.exception(
            "Report transaction rolled back for '%s'.",
            report_name,
        )

        raise

    # ========================================================================
    # 7. SUMMARY
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

    print(
        f"  Lineage: {lineage_records}"
    )

    if extracted_fields != loaded_fields:

        logger.warning(
            "Extracted fields (%d) != loaded fields (%d).",
            extracted_fields,
            loaded_fields,
        )

    if extracted_filters != loaded_filters:

        logger.warning(
            "Extracted filters (%d) != loaded filters (%d).",
            extracted_filters,
            loaded_filters,
        )

    return {
        "report_name": report_name,
        "report_id": report_id,
        "semantic_model_id": fabric_semantic_model_id,
        "pages": len(pages),
        "visuals": len(visuals),
        "fields": loaded_fields,
        "filters": loaded_filters,
        "lineage": lineage_records,
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
    total_lineage = 0

    try:

        print()
        print(
            "Loading enabled Fabric workspaces from config..."
        )

        workspaces = load_enabled_workspaces()

        print(
            f"Enabled workspaces: {len(workspaces)}"
        )

        for index, workspace in enumerate(
            workspaces,
            start=1,
        ):

            print(
                f"  {index}. {workspace['workspace_name']} | "
                f"{workspace['workspace_id']}"
            )

        print()
        print(
            "Initializing Fabric REST API client..."
        )

        client = FabricClient()

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
        # SHARED LOOKUPS
        # ====================================================================

        print()
        print(
            "Building shared repository lookups..."
        )

        repository_models_cache = (
            get_repository_semantic_models(
                cursor
            )
        )

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

        print(
            f"Cached semantic models: {len(repository_models_cache)}"
        )

        print(
            f"Cached semantic tables: {len(semantic_table_lookup)}"
        )

        print(
            f"Cached semantic columns: {len(semantic_column_lookup)}"
        )

        print(
            f"Cached measures: {len(measure_lookup)}"
        )

        total_reports_discovered = 0

        for workspace in workspaces:

            workspace_id = workspace[
                "workspace_id"
            ]

            workspace_name = workspace[
                "workspace_name"
            ]

            print()
            print("#" * 70)
            print(
                f"WORKSPACE: {workspace_name}"
            )
            print(
                f"WORKSPACE ID: {workspace_id}"
            )
            print("#" * 70)

            workspace_items = get_workspace_items(
                client,
                workspace_id,
            )

            print_workspace_inventory(
                workspace_items
            )

            reports = discover_reports_from_items(
                workspace_items
            )

            if TARGET_REPORT_NAME:

                reports = [
                    report
                    for report in reports
                    if report.get("name", "").strip().lower()
                    == TARGET_REPORT_NAME.strip().lower()
                ]

                logger.info(
                    "DEBUG MODE: Processing only report '%s' (%d report found)",
                    TARGET_REPORT_NAME,
                    len(reports),
                )



            total_reports_discovered += len(
                reports
            )

            print(
                f"Reports discovered in workspace: "
                f"{len(reports)}"
            )

            for report in reports:

                try:

                    result = process_report(
                        client,
                        cursor,
                        repository_connection,
                        report,
                        workspace_id,
                        workspace_name,
                        workspace_items,
                        repository_models_cache,
                        semantic_table_lookup,
                        semantic_column_lookup,
                        measure_lookup,
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

                    total_lineage += result[
                        "lineage"
                    ]

                except Exception as exc:

                    if is_connection_failure(exc):

                        logger.warning(
                            "Repository connection lost while processing "
                            "'%s'. Reconnecting before continuing.",
                            report["name"],
                        )

                        try:

                            repository_connection.close()

                        except Exception as close_error:

                            logger.warning(
                                "Could not close lost repository "
                                "connection: %s",
                                close_error,
                            )

                        try:

                            repository_connection = (
                                connect_to_fabric_warehouse(
                                    DEFAULT_DRIVER,
                                    FABRIC_SQL_SERVER,
                                    FABRIC_SQL_DATABASE,
                                )
                            )

                            cursor = repository_connection.cursor()

                            logger.info(
                                "Reconnected to MetadataRepository."
                            )

                        except Exception as reconnect_error:

                            logger.exception(
                                "Could not reconnect to MetadataRepository."
                            )

                            print()
                            print(
                                "ERROR reconnecting to MetadataRepository: "
                                f"{reconnect_error}"
                            )

                    failed.append(
                        {
                            "report": report,
                            "workspace_id": workspace_id,
                            "workspace_name": workspace_name,
                            "error": str(exc),
                        }
                    )

                    logger.exception(
                        "Failed processing report '%s' in workspace '%s'.",
                        report["name"],
                        workspace_name,
                    )

                    print()
                    print(
                        f"ERROR processing report "
                        f"'{report['name']}' in "
                        f"workspace '{workspace_name}': "
                        f"{exc}"
                    )

            deleted_reports = reconcile_deleted_reports(
                cursor,
                workspace_id,
                [
                    report["id"]
                    for report in reports
                ],
            )

            repository_connection.commit()

            if deleted_reports:

                logger.info(
                    "Removed %d deleted reports from workspace '%s'.",
                    deleted_reports,
                    workspace_name,
                )

        print()
        print("=" * 70)
        print(
            "REPORT METADATA EXTRACTION COMPLETED"
        )
        print("=" * 70)

        print(
            f"Configured workspaces: {len(workspaces)}"
        )

        print(
            f"Reports discovered:    "
            f"{total_reports_discovered}"
        )

        print(
            f"Reports successful:    "
            f"{len(successful)}"
        )

        print(
            f"Reports failed:        "
            f"{len(failed)}"
        )

        print(
            f"Pages extracted:       "
            f"{total_pages}"
        )

        print(
            f"Visuals extracted:     "
            f"{total_visuals}"
        )

        print(
            f"Fields extracted:      "
            f"{total_fields}"
        )

        print(
            f"Filters extracted:     "
            f"{total_filters}"
        )

        print(
            f"Lineage records:       "
            f"{total_lineage}"
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
                    f"- {failure['report']['name']} | "
                    f"{failure['workspace_name']} | "
                    f"{failure['error']}"
                )

        if successful:

            print()
            print(
                "Metadata successfully loaded into MetadataRepository."
            )

        return (
            0
            if not failed
            else 1
        )

    except Exception as exc:

        logger.exception(
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

            logger.info(
                "Closed MetadataRepository connection."
            )


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":

    raise SystemExit(
        main()
    )