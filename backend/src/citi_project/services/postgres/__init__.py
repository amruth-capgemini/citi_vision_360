"""PostgreSQL as the structured source: one schema per simulated source system."""

from .config import PostgresConfig, PostgresConfigurationError, PostgresOperationError
from .tables import READER_ROLE, TABLES

__all__ = ["PostgresConfig", "PostgresConfigurationError", "PostgresOperationError", "READER_ROLE", "TABLES"]
