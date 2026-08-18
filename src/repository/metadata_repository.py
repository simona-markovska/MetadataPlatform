"""
Metadata Intelligence Platform
MetadataRepository data-access layer.

This module contains database queries only.

It does NOT contain:
    - MCP logic
    - AI logic
    - report interpretation
    - business decisions

The purpose is to provide reusable read-only access
to the MetadataRepository Fabric Warehouse.
"""

from __future__ import annotations

from typing import Any

from src.repository.connection import get_connection


# ============================================================================
# HELPERS
# ============================================================================

def rows_to_dicts(cursor) -> list[dict[str, Any]]:
    """Convert the current cursor result into dictionaries."""

    if cursor.description is None:
        return []

    columns = [
        column[0]
        for column in cursor.description
    ]

    rows = cursor.fetchall()

    return [
        dict(zip(columns, row))
        for row in rows
    ]


def execute_query(
    sql: str,
    parameters: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """
    Execute a read-only SQL query.

    This repository layer intentionally supports SELECT queries only.
    """

    connection = None

    try:
        connection = get_connection()

        cursor = connection.cursor()

        cursor.execute(
            sql,
            parameters,
        )

        return rows_to_dicts(cursor)

    finally:

        if connection:
            connection.close()


# ============================================================================
# REPOSITORY
# ============================================================================

class MetadataRepository:
    """
    Read-only access to MetadataRepository.
    """

    # ========================================================================
    # REPOSITORY SUMMARY
    # ========================================================================

    def get_repository_summary(self) -> dict[str, Any]:
        """Return high-level metadata counts."""

        queries = {

            "databases": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataDatabase
            """,

            "tables": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataTable
            """,

            "columns": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataColumn
            """,

            "semantic_models": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataSemanticModel
            """,

            "semantic_tables": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataSemanticTable
            """,

            "semantic_columns": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataSemanticColumn
            """,

            "measures": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataMeasure
            """,

            "semantic_relationships": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataSemanticRelationship
            """,

            "reports": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataReport
            """,

            "pages": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataReportPage
            """,

            "visuals": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataReportVisual
            """,

            "visual_fields": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataReportVisualField
            """,

            "visual_filters": """
                SELECT COUNT(*) AS Count
                FROM dbo.MetadataReportVisualFilter
            """,
        }

        result: dict[str, Any] = {}

        for name, sql in queries.items():

            rows = execute_query(sql)

            if rows:
                result[name] = rows[0]["Count"]
            else:
                result[name] = 0

        return result

    # ========================================================================
    # REPORTS
    # ========================================================================

    def list_reports(self) -> list[dict[str, Any]]:
        """Return all reports stored in MetadataRepository."""

        sql = """
            SELECT
                r.ReportID,
                r.ReportName,
                r.WorkspaceID,
                r.WorkspaceName,
                r.FabricReportID,
                r.SemanticModelID,
                sm.ModelName,
                sm.FabricModelID,
                r.SourceType

            FROM dbo.MetadataReport r

            LEFT JOIN dbo.MetadataSemanticModel sm
                ON r.SemanticModelID = sm.SemanticModelID

            ORDER BY
                r.ReportName
        """

        return execute_query(sql)

    # ========================================================================
    # REPORT METADATA
    # ========================================================================

    def get_report_metadata(
        self,
        report_name: str,
    ) -> dict[str, Any]:
        """
        Return complete structural metadata for a report.

        Includes:
            - report
            - pages
            - visuals
            - visual fields
            - visual filters
        """

        # --------------------------------------------------------------------
        # REPORT
        # --------------------------------------------------------------------

        report_sql = """
            SELECT
                r.ReportID,
                r.ReportName,
                r.WorkspaceID,
                r.WorkspaceName,
                r.FabricReportID,
                r.SemanticModelID,
                sm.ModelName,
                sm.FabricModelID,
                r.SourceType

            FROM dbo.MetadataReport r

            LEFT JOIN dbo.MetadataSemanticModel sm
                ON r.SemanticModelID = sm.SemanticModelID

            WHERE r.ReportName = ?
        """

        report_rows = execute_query(
            report_sql,
            (report_name,),
        )

        if not report_rows:

            return {
                "found": False,
                "report_name": report_name,
                "message": (
                    f"Report '{report_name}' was not found."
                ),
            }

        report = report_rows[0]

        report_id = report["ReportID"]

        # --------------------------------------------------------------------
        # PAGES
        # --------------------------------------------------------------------

        pages_sql = """
            SELECT
                PageID,
                ReportID,
                PageName,
                DisplayName,
                PageOrder

            FROM dbo.MetadataReportPage

            WHERE ReportID = ?

            ORDER BY
                PageOrder,
                PageName
        """

        pages = execute_query(
            pages_sql,
            (report_id,),
        )

        # --------------------------------------------------------------------
        # VISUALS
        # --------------------------------------------------------------------

        visuals_sql = """
            SELECT
                v.VisualID,
                v.PageID,
                p.PageName,
                p.DisplayName AS PageDisplayName,
                v.FabricVisualID,
                v.VisualType

            FROM dbo.MetadataReportVisual v

            INNER JOIN dbo.MetadataReportPage p
                ON v.PageID = p.PageID

            WHERE p.ReportID = ?

            ORDER BY
                p.PageOrder,
                v.VisualID
        """

        visuals = execute_query(
            visuals_sql,
            (report_id,),
        )

        # --------------------------------------------------------------------
        # VISUAL FIELDS
        # --------------------------------------------------------------------

        fields_sql = """
            SELECT
                vf.VisualFieldID,
                vf.VisualID,
                v.VisualType,
                p.PageName,
                vf.FieldType,

                st.TableName AS SemanticTable,
                sc.ColumnName AS SemanticColumn,

                m.MeasureName,

                vf.AggregationFunction,
                vf.ProjectionArea,
                vf.QueryRef,
                vf.NativeQueryRef

            FROM dbo.MetadataReportVisualField vf

            INNER JOIN dbo.MetadataReportVisual v
                ON vf.VisualID = v.VisualID

            INNER JOIN dbo.MetadataReportPage p
                ON v.PageID = p.PageID

            LEFT JOIN dbo.MetadataSemanticTable st
                ON vf.SemanticTableID = st.SemanticTableID

            LEFT JOIN dbo.MetadataSemanticColumn sc
                ON vf.SemanticColumnID = sc.SemanticColumnID

            LEFT JOIN dbo.MetadataMeasure m
                ON vf.MeasureID = m.MeasureID

            WHERE p.ReportID = ?

            ORDER BY
                p.PageOrder,
                vf.VisualID,
                vf.VisualFieldID
        """

        visual_fields = execute_query(
            fields_sql,
            (report_id,),
        )

        # --------------------------------------------------------------------
        # VISUAL FILTERS
        # --------------------------------------------------------------------

        filters_sql = """
            SELECT
                f.VisualFilterID,
                f.VisualID,
                v.VisualType,
                p.PageName,

                f.FilterName,
                f.FieldType,

                st.TableName AS SemanticTable,
                sc.ColumnName AS SemanticColumn,

                m.MeasureName,

                f.FilterType

            FROM dbo.MetadataReportVisualFilter f

            INNER JOIN dbo.MetadataReportVisual v
                ON f.VisualID = v.VisualID

            INNER JOIN dbo.MetadataReportPage p
                ON v.PageID = p.PageID

            LEFT JOIN dbo.MetadataSemanticTable st
                ON f.SemanticTableID = st.SemanticTableID

            LEFT JOIN dbo.MetadataSemanticColumn sc
                ON f.SemanticColumnID = sc.SemanticColumnID

            LEFT JOIN dbo.MetadataMeasure m
                ON f.MeasureID = m.MeasureID

            WHERE p.ReportID = ?

            ORDER BY
                p.PageOrder,
                f.VisualID,
                f.VisualFilterID
        """

        visual_filters = execute_query(
            filters_sql,
            (report_id,),
        )

        return {
            "found": True,
            "report": report,
            "pages": pages,
            "visuals": visuals,
            "visual_fields": visual_fields,
            "visual_filters": visual_filters,

            "summary": {
                "pages": len(pages),
                "visuals": len(visuals),
                "visual_fields": len(visual_fields),
                "visual_filters": len(visual_filters),
            },
        }

    # ========================================================================
    # REPORT VISUALS
    # ========================================================================

    def get_report_visuals(
        self,
        report_name: str,
    ) -> list[dict[str, Any]]:
        """Return all visuals in a report together with their fields."""

        sql = """
            SELECT

                v.VisualID,

                p.PageName,
                p.DisplayName AS PageDisplayName,
                p.PageOrder,

                v.FabricVisualID,
                v.VisualType,

                vf.VisualFieldID,
                vf.FieldType,

                st.TableName AS SemanticTable,
                sc.ColumnName AS SemanticColumn,

                m.MeasureName,

                vf.AggregationFunction,
                vf.ProjectionArea,
                vf.QueryRef,
                vf.NativeQueryRef

            FROM dbo.MetadataReport r

            INNER JOIN dbo.MetadataReportPage p
                ON r.ReportID = p.ReportID

            INNER JOIN dbo.MetadataReportVisual v
                ON p.PageID = v.PageID

            LEFT JOIN dbo.MetadataReportVisualField vf
                ON v.VisualID = vf.VisualID

            LEFT JOIN dbo.MetadataSemanticTable st
                ON vf.SemanticTableID = st.SemanticTableID

            LEFT JOIN dbo.MetadataSemanticColumn sc
                ON vf.SemanticColumnID = sc.SemanticColumnID

            LEFT JOIN dbo.MetadataMeasure m
                ON vf.MeasureID = m.MeasureID

            WHERE r.ReportName = ?

            ORDER BY
                p.PageOrder,
                v.VisualID,
                vf.VisualFieldID
        """

        return execute_query(
            sql,
            (report_name,),
        )

    # ========================================================================
    # SEMANTIC MODEL
    # ========================================================================

    def get_semantic_model(
        self,
        model_name: str,
    ) -> dict[str, Any]:
        """
        Return semantic model structure.

        Includes:
            - semantic model
            - tables
            - columns
            - measures
            - relationships
        """

        model_sql = """
            SELECT
                SemanticModelID,
                ModelName,
                WorkspaceID,
                WorkspaceName,
                FabricModelID,
                SourceType

            FROM dbo.MetadataSemanticModel

            WHERE ModelName = ?
        """

        model_rows = execute_query(
            model_sql,
            (model_name,),
        )

        if not model_rows:

            return {
                "found": False,
                "model_name": model_name,
            }

        model = model_rows[0]

        model_id = model["SemanticModelID"]

        # --------------------------------------------------------------------
        # TABLES
        # --------------------------------------------------------------------

        tables_sql = """
            SELECT
                SemanticTableID,
                TableName,
                TableType,
                DefinitionPath

            FROM dbo.MetadataSemanticTable

            WHERE SemanticModelID = ?

            ORDER BY
                TableName
        """

        tables = execute_query(
            tables_sql,
            (model_id,),
        )

        # --------------------------------------------------------------------
        # COLUMNS
        # --------------------------------------------------------------------

        columns_sql = """
            SELECT
                sc.SemanticColumnID,
                st.TableName AS SemanticTable,
                sc.ColumnName,
                sc.DefinitionPath

            FROM dbo.MetadataSemanticColumn sc

            INNER JOIN dbo.MetadataSemanticTable st
                ON sc.SemanticTableID = st.SemanticTableID

            WHERE st.SemanticModelID = ?

            ORDER BY
                st.TableName,
                sc.ColumnName
        """

        columns = execute_query(
            columns_sql,
            (model_id,),
        )

        # --------------------------------------------------------------------
        # MEASURES
        # --------------------------------------------------------------------

        measures_sql = """
            SELECT
                m.MeasureID,
                m.MeasureName,
                m.DAXExpression,

                st.TableName AS SemanticTable,

                m.DefinitionPath

            FROM dbo.MetadataMeasure m

            LEFT JOIN dbo.MetadataSemanticTable st
                ON m.SemanticTableID = st.SemanticTableID

            WHERE m.SemanticModelID = ?

            ORDER BY
                m.MeasureName
        """

        measures = execute_query(
            measures_sql,
            (model_id,),
        )

        # --------------------------------------------------------------------
        # RELATIONSHIPS
        # --------------------------------------------------------------------

        relationships_sql = """
            SELECT
                sr.SemanticRelationshipID,

                ft.TableName AS FromTable,
                fc.ColumnName AS FromColumn,

                tt.TableName AS ToTable,
                tc.ColumnName AS ToColumn

            FROM dbo.MetadataSemanticRelationship sr

            INNER JOIN dbo.MetadataSemanticTable ft
                ON sr.FromTableID = ft.SemanticTableID

            INNER JOIN dbo.MetadataSemanticColumn fc
                ON sr.FromColumnID = fc.SemanticColumnID

            INNER JOIN dbo.MetadataSemanticTable tt
                ON sr.ToTableID = tt.SemanticTableID

            INNER JOIN dbo.MetadataSemanticColumn tc
                ON sr.ToColumnID = tc.SemanticColumnID

            WHERE sr.SemanticModelID = ?

            ORDER BY
                ft.TableName,
                fc.ColumnName
        """

        relationships = execute_query(
            relationships_sql,
            (model_id,),
        )

        return {
            "found": True,

            "semantic_model": model,

            "tables": tables,

            "columns": columns,

            "measures": measures,

            "relationships": relationships,

            "summary": {
                "tables": len(tables),
                "columns": len(columns),
                "measures": len(measures),
                "relationships": len(relationships),
            },
        }

    # ========================================================================
    # MEASURE
    # ========================================================================

    def get_measure(
        self,
        measure_name: str,
    ) -> dict[str, Any]:
        """
        Return a measure, DAX expression and dependency metadata.
        """

        measure_sql = """
            SELECT
                m.MeasureID,
                m.MeasureName,
                m.DAXExpression,
                m.DefinitionPath,

                sm.SemanticModelID,
                sm.ModelName,
                sm.FabricModelID,

                st.SemanticTableID,
                st.TableName AS SemanticTable,
                st.TableType

            FROM dbo.MetadataMeasure m

            INNER JOIN dbo.MetadataSemanticModel sm
                ON m.SemanticModelID = sm.SemanticModelID

            LEFT JOIN dbo.MetadataSemanticTable st
                ON m.SemanticTableID = st.SemanticTableID

            WHERE m.MeasureName = ?
        """

        measure_rows = execute_query(
            measure_sql,
            (measure_name,),
        )

        if not measure_rows:

            return {
                "found": False,
                "measure_name": measure_name,
            }

        measure = measure_rows[0]

        measure_id = measure["MeasureID"]

        # --------------------------------------------------------------------
        # CURRENT DEPENDENCY TABLE
        # --------------------------------------------------------------------

        dependencies_sql = """
            SELECT
                d.DependencyID,
                d.MeasureID,

                st.TableName AS SemanticTable,

                sc.ColumnName AS SemanticColumn,

                d.DependencyType,
                d.DependencyExpression

            FROM dbo.MetadataSemanticMeasureDependency d

            LEFT JOIN dbo.MetadataSemanticTable st
                ON d.SemanticTableID = st.SemanticTableID

            LEFT JOIN dbo.MetadataSemanticColumn sc
                ON d.SemanticColumnID = sc.SemanticColumnID

            WHERE d.MeasureID = ?

            ORDER BY
                d.DependencyID
        """

        dependencies = execute_query(
            dependencies_sql,
            (measure_id,),
        )

        # --------------------------------------------------------------------
        # FALLBACK TO OLD DEPENDENCY TABLE
        # --------------------------------------------------------------------

        if not dependencies:

            fallback_sql = """
                SELECT
                    d.MeasureDependencyID,
                    d.MeasureID,

                    st.TableName AS SemanticTable,

                    sc.ColumnName AS SemanticColumn,

                    d.DependencyType

                FROM dbo.MetadataMeasureDependency d

                LEFT JOIN dbo.MetadataSemanticTable st
                    ON d.SemanticTableID = st.SemanticTableID

                LEFT JOIN dbo.MetadataSemanticColumn sc
                    ON d.SemanticColumnID = sc.SemanticColumnID

                WHERE d.MeasureID = ?

                ORDER BY
                    d.MeasureDependencyID
            """

            dependencies = execute_query(
                fallback_sql,
                (measure_id,),
            )

        return {
            "found": True,

            "measure": measure,

            "dependencies": dependencies,

            "dependency_count": len(dependencies),
        }

    # ========================================================================
    # MEASURE LINEAGE
    # ========================================================================

    def get_measure_lineage(
        self,
        measure_name: str,
    ) -> dict[str, Any]:
        """
        Trace a semantic model measure to its physical SQL source.

        Lineage:

            Measure
                ->
            Semantic Measure Dependency
                ->
            Semantic Table / Column
                ->
            Semantic Column Source
                ->
            Physical Column
                ->
            Physical Table
                ->
            Physical Database
        """

        measure_sql = """
            SELECT
                m.MeasureID,
                m.MeasureName,
                m.DAXExpression,
                m.DefinitionPath,

                sm.SemanticModelID,
                sm.ModelName,
                sm.FabricModelID,

                st.SemanticTableID,
                st.TableName AS MeasureTable

            FROM dbo.MetadataMeasure m

            INNER JOIN dbo.MetadataSemanticModel sm
                ON m.SemanticModelID = sm.SemanticModelID

            LEFT JOIN dbo.MetadataSemanticTable st
                ON m.SemanticTableID = st.SemanticTableID

            WHERE m.MeasureName = ?
        """

        measure_rows = execute_query(
            measure_sql,
            (measure_name,),
        )

        if not measure_rows:

            return {
                "found": False,
                "measure_name": measure_name,
                "lineage": [],
            }

        measure = measure_rows[0]

        measure_id = measure["MeasureID"]

        # --------------------------------------------------------------------
        # PRIMARY LINEAGE
        # --------------------------------------------------------------------

        lineage_sql = """
            SELECT DISTINCT

                m.MeasureID,
                m.MeasureName,
                m.DAXExpression,

                sm.SemanticModelID,
                sm.ModelName,
                sm.FabricModelID,

                st.SemanticTableID,
                st.TableName AS SemanticTable,
                st.TableType,

                sc.SemanticColumnID,
                sc.ColumnName AS SemanticColumn,

                d.DependencyType,
                d.DependencyExpression,

                c.ColumnID,
                c.ColumnName AS SourceColumn,
                c.DataType,
                c.MaxLength,
                c.IsNullable,

                t.TableID,
                t.SchemaName AS SourceSchema,
                t.TableName AS SourceTable,
                t.TableType AS SourceTableType,

                db.DatabaseID,
                db.DatabaseName,
                db.ServerName

            FROM dbo.MetadataMeasure m

            INNER JOIN dbo.MetadataSemanticModel sm
                ON m.SemanticModelID = sm.SemanticModelID

            LEFT JOIN dbo.MetadataSemanticMeasureDependency d
                ON m.MeasureID = d.MeasureID

            LEFT JOIN dbo.MetadataSemanticTable st
                ON d.SemanticTableID = st.SemanticTableID

            LEFT JOIN dbo.MetadataSemanticColumn sc
                ON d.SemanticColumnID = sc.SemanticColumnID

            LEFT JOIN dbo.MetadataSemanticColumnSource scs
                ON sc.SemanticColumnID = scs.SemanticColumnID

            LEFT JOIN dbo.MetadataColumn c
                ON scs.ColumnID = c.ColumnID

            LEFT JOIN dbo.MetadataTable t
                ON c.TableID = t.TableID

            LEFT JOIN dbo.MetadataDatabase db
                ON t.DatabaseID = db.DatabaseID

            WHERE m.MeasureID = ?

            ORDER BY
                st.TableName,
                sc.ColumnName,
                t.SchemaName,
                t.TableName,
                c.ColumnName
        """

        lineage = execute_query(
            lineage_sql,
            (measure_id,),
        )

        # --------------------------------------------------------------------
        # FALLBACK LINEAGE
        # --------------------------------------------------------------------

        if not lineage:

            fallback_sql = """
                SELECT DISTINCT

                    m.MeasureID,
                    m.MeasureName,
                    m.DAXExpression,

                    sm.SemanticModelID,
                    sm.ModelName,
                    sm.FabricModelID,

                    st.SemanticTableID,
                    st.TableName AS SemanticTable,

                    sc.SemanticColumnID,
                    sc.ColumnName AS SemanticColumn,

                    c.ColumnID,
                    c.ColumnName AS SourceColumn,

                    t.TableID,
                    t.SchemaName AS SourceSchema,
                    t.TableName AS SourceTable,

                    db.DatabaseID,
                    db.DatabaseName,
                    db.ServerName

                FROM dbo.MetadataMeasure m

                INNER JOIN dbo.MetadataSemanticModel sm
                    ON m.SemanticModelID = sm.SemanticModelID

                INNER JOIN dbo.MetadataSemanticColumnSource scs
                    ON 1 = 1

                INNER JOIN dbo.MetadataSemanticColumn sc
                    ON sc.SemanticColumnID =
                       scs.SemanticColumnID

                INNER JOIN dbo.MetadataSemanticTable st
                    ON st.SemanticTableID =
                       sc.SemanticTableID

                INNER JOIN dbo.MetadataColumn c
                    ON c.ColumnID = scs.ColumnID

                INNER JOIN dbo.MetadataTable t
                    ON t.TableID = c.TableID

                INNER JOIN dbo.MetadataDatabase db
                    ON db.DatabaseID = t.DatabaseID

                WHERE m.SemanticModelID = sm.SemanticModelID

                  AND (
                        m.DAXExpression LIKE
                            '%' + sc.ColumnName + '%'
                      )

                ORDER BY
                    st.TableName,
                    sc.ColumnName
            """

            lineage = execute_query(
                fallback_sql
            )

        return {
            "found": True,

            "measure": measure,

            "lineage": lineage,

            "lineage_count": len(lineage),
        }

    # ========================================================================
    # COLUMN LINEAGE
    # ========================================================================

    def get_column_lineage(
        self,
        semantic_table: str,
        semantic_column: str,
    ) -> dict[str, Any]:
        """Trace a semantic column to its physical SQL source."""

        sql = """
            SELECT DISTINCT

                st.SemanticTableID,
                st.TableName AS SemanticTable,
                st.TableType,

                sc.SemanticColumnID,
                sc.ColumnName AS SemanticColumn,

                c.ColumnID,
                c.ColumnName AS SourceColumn,
                c.DataType,
                c.MaxLength,
                c.IsNullable,

                t.TableID,
                t.SchemaName AS SourceSchema,
                t.TableName AS SourceTable,
                t.TableType AS SourceTableType,

                db.DatabaseID,
                db.DatabaseName,
                db.ServerName

            FROM dbo.MetadataSemanticTable st

            INNER JOIN dbo.MetadataSemanticColumn sc
                ON st.SemanticTableID =
                   sc.SemanticTableID

            LEFT JOIN dbo.MetadataSemanticColumnSource scs
                ON sc.SemanticColumnID =
                   scs.SemanticColumnID

            LEFT JOIN dbo.MetadataColumn c
                ON scs.ColumnID =
                   c.ColumnID

            LEFT JOIN dbo.MetadataTable t
                ON c.TableID =
                   t.TableID

            LEFT JOIN dbo.MetadataDatabase db
                ON t.DatabaseID =
                   db.DatabaseID

            WHERE st.TableName = ?
              AND sc.ColumnName = ?
        """

        rows = execute_query(
            sql,
            (
                semantic_table,
                semantic_column,
            ),
        )

        return {
            "found": bool(rows),
            "semantic_table": semantic_table,
            "semantic_column": semantic_column,
            "lineage": rows,
            "lineage_count": len(rows),
        }

    # ========================================================================
    # FIND REPORTS USING MEASURE
    # ========================================================================

    def find_reports_using_measure(
        self,
        measure_name: str,
    ) -> dict[str, Any]:
        """
        Find every report visual and visual filter
        that uses a specific measure.
        """

        # --------------------------------------------------------------------
        # VISUAL USAGE
        # --------------------------------------------------------------------

        visual_sql = """
            SELECT DISTINCT

                r.ReportName,

                p.PageName,
                p.DisplayName AS PageDisplayName,

                v.VisualID,
                v.FabricVisualID,
                v.VisualType,

                vf.VisualFieldID,
                vf.FieldType,
                vf.ProjectionArea,

                'Visual Field' AS UsageType

            FROM dbo.MetadataMeasure m

            INNER JOIN dbo.MetadataReportVisualField vf
                ON m.MeasureID = vf.MeasureID

            INNER JOIN dbo.MetadataReportVisual v
                ON vf.VisualID = v.VisualID

            INNER JOIN dbo.MetadataReportPage p
                ON v.PageID = p.PageID

            INNER JOIN dbo.MetadataReport r
                ON p.ReportID = r.ReportID

            WHERE m.MeasureName = ?
        """

        visual_usage = execute_query(
            visual_sql,
            (measure_name,),
        )

        # --------------------------------------------------------------------
        # FILTER USAGE
        # --------------------------------------------------------------------

        filter_sql = """
            SELECT DISTINCT

                r.ReportName,

                p.PageName,
                p.DisplayName AS PageDisplayName,

                v.VisualID,
                v.FabricVisualID,
                v.VisualType,

                f.VisualFilterID,
                f.FilterName,
                f.FieldType,
                f.FilterType,

                'Visual Filter' AS UsageType

            FROM dbo.MetadataMeasure m

            INNER JOIN dbo.MetadataReportVisualFilter f
                ON m.MeasureID = f.MeasureID

            INNER JOIN dbo.MetadataReportVisual v
                ON f.VisualID = v.VisualID

            INNER JOIN dbo.MetadataReportPage p
                ON v.PageID = p.PageID

            INNER JOIN dbo.MetadataReport r
                ON p.ReportID = r.ReportID

            WHERE m.MeasureName = ?
        """

        filter_usage = execute_query(
            filter_sql,
            (measure_name,),
        )

        all_usage = visual_usage + filter_usage

        return {
            "measure_name": measure_name,

            "found": bool(all_usage),

            "visual_usage": visual_usage,

            "filter_usage": filter_usage,

            "total_usage_records": len(all_usage),

            "visual_usage_count": len(visual_usage),

            "filter_usage_count": len(filter_usage),

            "total_affected_visuals": len(
                {
                    (
                        row["ReportName"],
                        row["PageName"],
                        row["VisualID"],
                    )
                    for row in all_usage
                }
            ),

            "usage": all_usage,
        }

    # ========================================================================
    # FIND REPORTS USING COLUMN
    # ========================================================================

    def find_reports_using_column(
        self,
        semantic_table: str,
        semantic_column: str,
    ) -> list[dict[str, Any]]:
        """Find report visuals that directly use a semantic column."""

        sql = """
            SELECT DISTINCT

                r.ReportName,

                p.PageName,
                p.DisplayName AS PageDisplayName,

                v.VisualID,
                v.VisualType,

                vf.VisualFieldID,
                vf.FieldType,
                vf.AggregationFunction,
                vf.ProjectionArea

            FROM dbo.MetadataSemanticTable st

            INNER JOIN dbo.MetadataSemanticColumn sc
                ON st.SemanticTableID =
                   sc.SemanticTableID

            INNER JOIN dbo.MetadataReportVisualField vf
                ON sc.SemanticColumnID =
                   vf.SemanticColumnID

            INNER JOIN dbo.MetadataReportVisual v
                ON vf.VisualID =
                   v.VisualID

            INNER JOIN dbo.MetadataReportPage p
                ON v.PageID =
                   p.PageID

            INNER JOIN dbo.MetadataReport r
                ON p.ReportID =
                   r.ReportID

            WHERE st.TableName = ?
              AND sc.ColumnName = ?

            ORDER BY
                r.ReportName,
                p.PageOrder,
                v.VisualID
        """

        return execute_query(
            sql,
            (
                semantic_table,
                semantic_column,
            ),
        )

    # ========================================================================
    # FIND UNUSED MEASURES
    # ========================================================================

    def find_unused_measures(
        self,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Find measures that are not used by any report visual
        or visual-level filter.
        """

        if model_name:

            unused_sql = """
                SELECT

                    m.MeasureID,
                    m.MeasureName,
                    m.DAXExpression,

                    sm.ModelName,

                    st.TableName AS SemanticTable,

                    m.DefinitionPath

                FROM dbo.MetadataMeasure m

                INNER JOIN dbo.MetadataSemanticModel sm
                    ON m.SemanticModelID =
                       sm.SemanticModelID

                LEFT JOIN dbo.MetadataSemanticTable st
                    ON m.SemanticTableID =
                       st.SemanticTableID

                LEFT JOIN dbo.MetadataReportVisualField vf
                    ON m.MeasureID =
                       vf.MeasureID

                LEFT JOIN dbo.MetadataReportVisualFilter vfilter
                    ON m.MeasureID =
                       vfilter.MeasureID

                WHERE sm.ModelName = ?

                GROUP BY
                    m.MeasureID,
                    m.MeasureName,
                    m.DAXExpression,
                    sm.ModelName,
                    st.TableName,
                    m.DefinitionPath

                HAVING
                    COUNT(DISTINCT vf.VisualFieldID) = 0
                    AND COUNT(DISTINCT vfilter.VisualFilterID) = 0

                ORDER BY
                    m.MeasureName
            """

            unused = execute_query(
                unused_sql,
                (model_name,),
            )

            total_sql = """
                SELECT
                    COUNT(*) AS Count

                FROM dbo.MetadataMeasure m

                INNER JOIN dbo.MetadataSemanticModel sm
                    ON m.SemanticModelID =
                       sm.SemanticModelID

                WHERE sm.ModelName = ?
            """

            total_rows = execute_query(
                total_sql,
                (model_name,),
            )

        else:

            unused_sql = """
                SELECT

                    m.MeasureID,
                    m.MeasureName,
                    m.DAXExpression,

                    sm.ModelName,

                    st.TableName AS SemanticTable,

                    m.DefinitionPath

                FROM dbo.MetadataMeasure m

                INNER JOIN dbo.MetadataSemanticModel sm
                    ON m.SemanticModelID =
                       sm.SemanticModelID

                LEFT JOIN dbo.MetadataSemanticTable st
                    ON m.SemanticTableID =
                       st.SemanticTableID

                LEFT JOIN dbo.MetadataReportVisualField vf
                    ON m.MeasureID =
                       vf.MeasureID

                LEFT JOIN dbo.MetadataReportVisualFilter vfilter
                    ON m.MeasureID =
                       vfilter.MeasureID

                GROUP BY
                    m.MeasureID,
                    m.MeasureName,
                    m.DAXExpression,
                    sm.ModelName,
                    st.TableName,
                    m.DefinitionPath

                HAVING
                    COUNT(DISTINCT vf.VisualFieldID) = 0
                    AND COUNT(DISTINCT vfilter.VisualFilterID) = 0

                ORDER BY
                    sm.ModelName,
                    m.MeasureName
            """

            unused = execute_query(
                unused_sql
            )

            total_sql = """
                SELECT
                    COUNT(*) AS Count

                FROM dbo.MetadataMeasure
            """

            total_rows = execute_query(
                total_sql
            )

        total_measures = (
            total_rows[0]["Count"]
            if total_rows
            else 0
        )

        used_measures = (
            total_measures -
            len(unused)
        )

        return {
            "total_measures": total_measures,

            "used_measures": used_measures,

            "unused_measure_count": len(unused),

            "unused_measures": unused,

            "summary": (
                f"{len(unused)} out of "
                f"{total_measures} measures "
                f"are unused."
            ),
        }