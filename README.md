# Metadata Platform

## Overview

Metadata Platform is an end-to-end metadata management solution designed to collect, store, and visualize metadata from multiple data sources.

The project started with SQL Server metadata extraction and is being developed into a centralized platform capable of managing metadata, tracking data lineage, automating metadata collection, and providing AI-assisted search and analysis.

The long-term goal is to build a modern metadata platform using Microsoft Fabric, Python, SQL Server, and Power BI that can serve as a lightweight data catalog for enterprise environments.

---

## Objectives

* Build a centralized metadata repository.
* Automatically extract metadata from multiple data sources.
* Track metadata extraction history.
* Provide data lineage and impact analysis.
* Create a web-based interface for browsing metadata.
* Develop an AI assistant capable of answering metadata-related questions using natural language.

---

## Current Features

### SQL Server Metadata Extraction

* Connects to SQL Server using Python.
* Extracts database, table, and column metadata.
* Reads metadata from `INFORMATION_SCHEMA`.
* Exports metadata to CSV files.
* Loads extracted metadata into a centralized metadata repository.

### Metadata Repository

The project maintains a dedicated SQL Server repository containing:

* MetadataDatabase
* MetadataTable
* MetadataColumn
* MetadataExtractionLog

The repository keeps track of:

* Registered source databases
* Tables and schemas
* Columns and data types
* Extraction history
* Execution status

### Logging

The extraction process records:

* Start time
* End time
* Status
* Number of processed tables
* Number of processed columns
* Error messages (if applicable)

### Configuration

The extractor supports configurable:

* SQL Server
* Source database
* Repository database
* ODBC driver
* Output directory

Configuration is separated from the extraction logic to improve maintainability.

---

## Technology Stack

* Python
* SQL Server
* pyodbc
* pandas
* Microsoft Fabric (planned)
* Power BI (planned)
* Git
* GitHub
* Visual Studio Code

---

## Project Structure

```text
MetadataPlatform/
│
├── README.md
├── .gitignore
│
├── config/
│   ├── __init__.py
│   └── config.py
│
├── docs/
│   └── images/
│
├── extractors/
│   ├── __init__.py
│   └── sql_extractor.py
│
├── output/
│
└── sql/
```

---

## Roadmap

### Phase 1 – SQL Metadata Extraction ✅

* [x] SQL Server connection
* [x] Metadata extraction
* [x] Metadata repository
* [x] CSV export
* [x] Extraction logging
* [x] Modular Python architecture

### Phase 2 – Microsoft Fabric

* [ ] Fabric Notebook
* [ ] Fabric Pipeline orchestration
* [ ] Scheduled metadata extraction

### Phase 3 – Metadata Expansion

* [ ] Views
* [ ] Primary keys
* [ ] Foreign keys
* [ ] Stored procedures
* [ ] Indexes

### Phase 4 – Lineage

* [ ] Table lineage
* [ ] Column lineage
* [ ] Dependency tracking
* [ ] Impact analysis

### Phase 5 – User Interface

* [ ] Metadata search
* [ ] Interactive dashboard
* [ ] Metadata explorer

### Phase 6 – AI Assistant

* [ ] Natural language search
* [ ] AI-generated documentation
* [ ] Lineage explanations
* [ ] Metadata recommendations

---

## Future Vision

The final platform will provide a centralized solution for discovering, documenting, and understanding enterprise data assets. Metadata will be collected automatically from multiple systems, stored in a unified repository, visualized through an intuitive interface, and enhanced with AI capabilities to help users explore data using natural language.

The project is being developed incrementally, with each phase introducing new capabilities while keeping the architecture modular and extensible.

---

## License

This project is intended for learning, experimentation, and portfolio purposes.
