import os
from pathlib import Path

DEFAULT_DRIVER = os.getenv('METADATA_SQL_DRIVER', 'ODBC Driver 18 for SQL Server')
DEFAULT_SERVER = os.getenv('METADATA_SQL_SERVER', 'AXM345')
DEFAULT_SOURCE_DATABASE = os.getenv('METADATA_SOURCE_DATABASE', 'AdventureWorks2022')
DEFAULT_REPOSITORY_DATABASE = os.getenv('METADATA_REPOSITORY_DATABASE', 'MetadataRepository')
DEFAULT_OUTPUT_DIR = os.getenv('METADATA_OUTPUT_DIR', str(Path(__file__).resolve().parents[1] / 'output'))
