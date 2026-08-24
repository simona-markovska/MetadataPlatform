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
    """
    Normalize names for intelligent report/semantic-model matching.

    Examples:

        HR Overview
        hr_overview
        HR-Overview

    all become:

        hroverview
    """

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
    """
    Convert a name into meaningful tokens.

    Example:

        SM_AdventureWorks_HR
        -> {"sm", "adventureworks", "hr"}

        HR Overview
        -> {"hr", "overview"}
    """

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
    """
    Remove generic words that are not useful for identifying a model.
    """

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
    """
    Calculate a confidence score between a report and semantic model.

    This is intentionally generic.

    Example:

        HR Overview
        SM_AdventureWorks_HR

    receives a strong score because both contain "hr".

    Example:

        Sales overview
        SM_AdventureWorks_Sales

    receives a strong score because both contain "sales".
    """

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

    # Strong signal: exact meaningful token overlap.
    score += len(intersection) * 100

    # Additional signal if normalized names overlap.
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
    )


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

    return pyodbc.connect(
        connection_string
    )


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
        key_normalized in SEMANTIC_ID_KEYS
        or (
            "semanticmodel" in key_normalized
            and "id" in key_normalized
        )
        or (
            "dataset" in key_normalized
            and "id" in key_normalized
        )
    )

    if not is_model_key:
        return

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
    """
    Score a semantic-model candidate.

    This deliberately avoids hard-coded mappings.

    Signals:

        1. Report name ↔ Fabric model name
        2. Report name ↔ repository model name
        3. Shared meaningful tokens
        4. Exact normalized match
    """

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
):
    """
    Resolve the semantic model connected to a report.

    Resolution order:

        1. Explicit semantic-model ID in report definition
        2. Explicit semantic-model ID in workspace report metadata
        3. Repository cross-reference
        4. Intelligent report/model name scoring
        5. Single-model fallback

    The important improvement is step 4.

    Previously:

        HR Overview
        Sales overview

    could not be associated with:

        SM_AdventureWorks_HR
        SM_AdventureWorks_Sales

    because Fabric's report definition did not contain an explicit
    semantic model ID.

    The new resolver uses the names as evidence instead.
    """

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

    # ========================================================================
    # 1. EXPLICIT ID FROM REPORT DEFINITION
    # ========================================================================

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

    # ========================================================================
    # 2. EXPLICIT ID FROM REPORT WORKSPACE ITEM
    # ========================================================================

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

    # ========================================================================
    # 3. REPOSITORY CROSS-REFERENCE
    # ========================================================================

    repository_models = (
        get_repository_semantic_models(
            cursor
        )
    )

    repository_by_fabric_id = {
        model["fabric_id"]: model
        for model in repository_models
        if model["fabric_id"]
    }

    # Explicit definition candidates that are present in repository.
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

    # ========================================================================
    # 4. INTELLIGENT NAME MATCHING
    # ========================================================================

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

    # ========================================================================
    # 5. SELECT STRONG UNIQUE MATCH
    # ========================================================================

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

            # Require a meaningful lead over the second candidate.
            #
            # This prevents:
            #
            # Report: Finance Overview
            #
            # Model 1: Finance Sales
            # Model 2: Finance HR
            #
            # from being selected merely because both contain "finance".
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

    # ========================================================================
    # 6. SINGLE MODEL FALLBACK
    # ========================================================================

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

    # ========================================================================
    # 7. FAIL SAFELY
    # ========================================================================

    available_models = ", ".join(
        f"{model['name']} [{model['id']}]"
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

    # ========================================================================
    # PAGES
    # ========================================================================

    def extract_pages(
        self,
    ):
        pages = []

        page_order = []

        for part in self.parts:

            if part.get("path") != (
                "definition/pages/pages.json"
            ):
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

        page_data = {}

        for part in self.parts:

            path = part.get(
                "path",
                "",
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

            if not data:
                continue

            page_id = (
                data.get("name")
                or match.group(1)
            )

            page_data[
                page_id
            ] = data

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

            page_name = match.group(1)

            visual_id_from_path = match.group(2)

            data = parse_json_part(
                part
            )

            if not data:
                continue

            visual_id = (
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
                or data.get(
                    "visualType"
                )
                or "Unknown"
            )

            visuals.append(
                {
                    "page_name": page_name,
                    "fabric_visual_id": visual_id,
                    "visual_type": visual_type,
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

                field_metadata = (
                    self._parse_field(
                        field
                    )
                )

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

            field_metadata = (
                self._parse_field(
                    filter_definition.get(
                        "field",
                        {},
                    )
                )
            )

            if field_metadata:

                result.append(
                    {
                        "filter_name":
                            filter_definition.get(
                                "name"
                            ),
                        "field_type":
                            field_metadata.get(
                                "field_type"
                            ),
                        "table_name":
                            field_metadata.get(
                                "table_name"
                            ),
                        "column_name":
                            field_metadata.get(
                                "column_name"
                            ),
                        "measure_name":
                            field_metadata.get(
                                "measure_name"
                            ),
                        "filter_type":
                            filter_definition.get(
                                "type"
                            ),
                    }
                )

            else:

                result.append(
                    {
                        "filter_name":
                            filter_definition.get(
                                "name"
                            ),
                        "field_type": None,
                        "table_name": None,
                        "column_name": None,
                        "measure_name": None,
                        "filter_type":
                            filter_definition.get(
                                "type"
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
            WORKSPACE_ID,
            WORKSPACE_NAME,
            semantic_model_id,
            "Microsoft Fabric Report",
            repository_report_id,
        )

        cursor.connection.commit()

        logger.info(
            "Updated MetadataReport %s",
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

    return int(result[0])


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

    cursor.connection.commit()


# ============================================================================
# LOAD PAGES
# ============================================================================

def load_pages(
    cursor,
    report_id,
    pages,
):

    lookup = {}

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
            SELECT PageID
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
                f"Could not retrieve PageID for "
                f"{page['page_name']}"
            )

        lookup[
            page["page_name"]
        ] = int(result[0])

    cursor.connection.commit()

    return lookup


# ============================================================================
# LOAD VISUALS
# ============================================================================

def load_visuals(
    cursor,
    page_lookup,
    visuals,
):

    lookup = {}

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
            SELECT VisualID
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
                "Could not retrieve VisualID."
            )

        lookup[
            visual["fabric_visual_id"]
        ] = int(result[0])

    cursor.connection.commit()

    return lookup


# ============================================================================
# FIELD ID RESOLUTION
# ============================================================================

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

    if table_name:

        semantic_table_id = (
            semantic_table_lookup.get(
                (
                    semantic_model_id,
                    str(table_name),
                )
            )
        )

    if table_name and column_name:

        semantic_column_id = (
            semantic_column_lookup.get(
                (
                    semantic_model_id,
                    str(table_name),
                    str(column_name),
                )
            )
        )

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
                field.get("field_type"),
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

    if unresolved:

        logger.warning(
            "Unresolved visual field references: %d",
            unresolved,
        )

    return inserted


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

    inserted = 0
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

    if unresolved:

        logger.warning(
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

        total_fields += len(fields)
        total_filters += len(filters)

    return (
        total_fields,
        total_filters,
    )


# ============================================================================
# PROCESS ONE REPORT
# ============================================================================

def process_report(
    client,
    cursor,
    report,
    workspace_items,
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
            workspace_items,
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
    # 4. REPORT
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
    # 5. PARSE
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
    # 6. REFRESH
    # ========================================================================

    clear_report_children(
        cursor,
        repository_report_id,
    )

    # ========================================================================
    # 7. PAGES
    # ========================================================================

    page_lookup = load_pages(
        cursor,
        repository_report_id,
        pages,
    )

    # ========================================================================
    # 8. VISUALS
    # ========================================================================

    visual_lookup = load_visuals(
        cursor,
        page_lookup,
        visuals,
    )

    # ========================================================================
    # 9. LOOKUPS
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
    # 10. FIELDS
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
    # 11. FILTERS
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
                workspace.get("displayName")
                or workspace.get("name")
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
        # 3. DISCOVER WORKSPACE ITEMS
        # ====================================================================

        print()
        print(
            "Discovering workspace items..."
        )

        workspace_items = get_workspace_items(
            client,
            WORKSPACE_ID,
        )

        print_workspace_inventory(
            workspace_items
        )

        # ====================================================================
        # 4. REPORT DISCOVERY
        # ====================================================================

        print()
        print(
            "Discovering reports automatically..."
        )

        reports = discover_reports_from_items(
            workspace_items
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
        # 5. CONNECT REPOSITORY
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
        # 6. PROCESS REPORTS
        # ====================================================================

        for report in reports:

            try:

                result = process_report(
                    client,
                    cursor,
                    report,
                    workspace_items,
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

                logger.exception(
                    "Failed processing report '%s'.",
                    report["name"],
                )

                print()
                print(
                    f"ERROR processing report "
                    f"'{report['name']}': {exc}"
                )

        # ====================================================================
        # 7. FINAL SUMMARY
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