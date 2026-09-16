-- ================================================================
-- MetadataRepository schema backup
-- Generated: 2026-09-16T11:22:16.393452
-- ================================================================

IF OBJECT_ID(N'dbo.MetadataColumn', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataColumn]
    (
        [ColumnID] bigint NOT NULL,
        [TableID] bigint NOT NULL,
        [ColumnName] varchar(255) NOT NULL,
        [DataType] varchar(100) NULL,
        [MaxLength] int NULL,
        [IsNullable] varchar(10) NULL,
        [SourceColumnKey] varchar(1500) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataDatabase', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataDatabase]
    (
        [DatabaseID] bigint NOT NULL,
        [DatabaseName] varchar(255) NOT NULL,
        [ServerName] varchar(255) NOT NULL,
        [SourceSystem] varchar(200) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataLineage', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataLineage]
    (
        [LineageID] bigint NOT NULL,
        [FromEntityType] varchar(100) NOT NULL,
        [FromEntityID] bigint NOT NULL,
        [ToEntityType] varchar(100) NOT NULL,
        [ToEntityID] bigint NOT NULL,
        [LineageType] varchar(100) NOT NULL,
        [Expression] varchar(MAX) NULL,
        [ResolutionMethod] varchar(100) NULL,
        [CreatedFromExtractionID] bigint NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataMeasure', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataMeasure]
    (
        [MeasureID] bigint NOT NULL,
        [SemanticModelID] bigint NOT NULL,
        [SemanticTableID] bigint NULL,
        [MeasureName] varchar(500) NOT NULL,
        [DAXExpression] varchar(MAX) NULL,
        [DefinitionPath] varchar(1000) NULL,
        [IsHidden] varchar(10) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataMeasureDependency', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataMeasureDependency]
    (
        [DependencyID] bigint NOT NULL,
        [MeasureID] bigint NOT NULL,
        [SemanticTableID] bigint NULL,
        [SemanticColumnID] bigint NULL,
        [MeasureDependencyID] bigint NULL,
        [DependencyType] varchar(100) NOT NULL,
        [DependencyExpression] varchar(MAX) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataRelationship', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataRelationship]
    (
        [RelationshipID] bigint NOT NULL,
        [DatabaseID] bigint NOT NULL,
        [ParentTableID] bigint NOT NULL,
        [ParentColumnID] bigint NULL,
        [ChildTableID] bigint NOT NULL,
        [ChildColumnID] bigint NULL,
        [ConstraintName] varchar(255) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataRelationshipColumn', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataRelationshipColumn]
    (
        [RelationshipColumnID] bigint NOT NULL,
        [RelationshipID] bigint NOT NULL,
        [ParentColumnID] bigint NOT NULL,
        [ChildColumnID] bigint NOT NULL,
        [ColumnOrdinal] int NOT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataReport', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataReport]
    (
        [ReportID] bigint NOT NULL,
        [ReportName] varchar(500) NOT NULL,
        [WorkspaceID] varchar(200) NULL,
        [WorkspaceName] varchar(500) NULL,
        [FabricReportID] varchar(200) NOT NULL,
        [SemanticModelID] bigint NULL,
        [SourceType] varchar(100) NOT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataReportPage', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataReportPage]
    (
        [PageID] bigint NOT NULL,
        [ReportID] bigint NOT NULL,
        [PageName] varchar(500) NOT NULL,
        [DisplayName] varchar(500) NULL,
        [PageOrder] int NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataReportVisual', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataReportVisual]
    (
        [VisualID] bigint NOT NULL,
        [PageID] bigint NOT NULL,
        [FabricVisualID] varchar(200) NOT NULL,
        [VisualType] varchar(200) NOT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataReportVisualField', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataReportVisualField]
    (
        [VisualFieldID] bigint NOT NULL,
        [VisualID] bigint NOT NULL,
        [FieldType] varchar(50) NOT NULL,
        [SemanticTableID] bigint NULL,
        [SemanticColumnID] bigint NULL,
        [MeasureID] bigint NULL,
        [AggregationFunction] varchar(100) NULL,
        [ProjectionArea] varchar(100) NULL,
        [QueryRef] varchar(1000) NULL,
        [NativeQueryRef] varchar(1000) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataReportVisualFilter', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataReportVisualFilter]
    (
        [VisualFilterID] bigint NOT NULL,
        [VisualID] bigint NOT NULL,
        [FilterName] varchar(500) NULL,
        [FieldType] varchar(50) NULL,
        [SemanticTableID] bigint NULL,
        [SemanticColumnID] bigint NULL,
        [MeasureID] bigint NULL,
        [FilterType] varchar(100) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataSemanticColumn', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataSemanticColumn]
    (
        [SemanticColumnID] bigint NOT NULL,
        [SemanticTableID] bigint NOT NULL,
        [ColumnName] varchar(500) NOT NULL,
        [DefinitionPath] varchar(1000) NULL,
        [ColumnType] varchar(100) NULL,
        [IsHidden] varchar(10) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataSemanticColumnDependency', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataSemanticColumnDependency]
    (
        [DependencyID] bigint NOT NULL,
        [SourceSemanticColumnID] bigint NOT NULL,
        [TargetSemanticTableID] bigint NULL,
        [TargetSemanticColumnID] bigint NULL,
        [DependencyType] varchar(100) NOT NULL,
        [DependencyExpression] varchar(MAX) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataSemanticColumnSource', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataSemanticColumnSource]
    (
        [SemanticColumnSourceID] bigint NOT NULL,
        [SemanticColumnID] bigint NOT NULL,
        [ColumnID] bigint NOT NULL,
        [ResolutionMethod] varchar(100) NULL,
        [SourceExpression] varchar(MAX) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataSemanticModel', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataSemanticModel]
    (
        [SemanticModelID] bigint NOT NULL,
        [ModelName] varchar(500) NOT NULL,
        [WorkspaceID] varchar(200) NULL,
        [WorkspaceName] varchar(500) NULL,
        [FabricModelID] varchar(200) NOT NULL,
        [SourceType] varchar(100) NOT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataSemanticRelationship', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataSemanticRelationship]
    (
        [SemanticRelationshipID] bigint NOT NULL,
        [SemanticModelID] bigint NOT NULL,
        [FromTableID] bigint NOT NULL,
        [FromColumnID] bigint NOT NULL,
        [ToTableID] bigint NOT NULL,
        [ToColumnID] bigint NOT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataSemanticTable', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataSemanticTable]
    (
        [SemanticTableID] bigint NOT NULL,
        [SemanticModelID] bigint NOT NULL,
        [TableName] varchar(500) NOT NULL,
        [TableType] varchar(100) NOT NULL,
        [DefinitionPath] varchar(1000) NULL,
        [IsHidden] varchar(10) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataSemanticTableDependency', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataSemanticTableDependency]
    (
        [DependencyID] bigint NOT NULL,
        [SemanticTableID] bigint NOT NULL,
        [TargetSemanticTableID] bigint NULL,
        [TargetSemanticColumnID] bigint NULL,
        [DependencyType] varchar(100) NOT NULL,
        [DependencyExpression] varchar(MAX) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataSemanticTableSource', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataSemanticTableSource]
    (
        [SemanticTableSourceID] bigint NOT NULL,
        [SemanticTableID] bigint NOT NULL,
        [TableID] bigint NOT NULL,
        [ResolutionMethod] varchar(100) NULL,
        [SourceExpression] varchar(MAX) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataTable', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataTable]
    (
        [TableID] bigint NOT NULL,
        [DatabaseID] bigint NOT NULL,
        [SchemaName] varchar(255) NOT NULL,
        [TableName] varchar(255) NOT NULL,
        [TableType] varchar(50) NULL,
        [SourceObjectKey] varchar(1000) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.MetadataWorkspace', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[MetadataWorkspace]
    (
        [WorkspaceID] varchar(100) NOT NULL,
        [WorkspaceName] varchar(500) NOT NULL,
        [IsEnabled] bit NOT NULL
    );
END;
GO

