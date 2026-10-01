"""Explicit environment configuration; no dotenv loading or credential defaults."""

from dataclasses import dataclass, field
import os


class PostgresConfigurationError(ValueError):
    pass


class PostgresOperationError(RuntimeError):
    """Sanitized database failure. Messages never contain DSNs, row contents or server text."""


@dataclass(frozen=True)
class PostgresConfig:
    owner: str = field(default="", repr=False)
    reader: str = field(default="", repr=False)

    def __post_init__(self):
        for name, value in (("CITI_PG_DSN", self.owner), ("CITI_PG_READER_DSN", self.reader)):
            if not isinstance(value, str) or any(ord(c) < 32 for c in value):
                raise PostgresConfigurationError(f"{name} is invalid")

    @property
    def owner_dsn(self):
        return self._required(self.owner, "CITI_PG_DSN")

    @property
    def reader_dsn(self):
        return self._required(self.reader, "CITI_PG_READER_DSN")

    @staticmethod
    def _required(value, name):
        if not value.strip():
            raise PostgresConfigurationError(f"{name} is required")
        try:
            from psycopg.conninfo import conninfo_to_dict
            conninfo_to_dict(value)
        except ImportError:
            raise PostgresConfigurationError("psycopg is not installed") from None
        except Exception:
            raise PostgresConfigurationError(f"{name} is not a valid connection string") from None
        return value

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        return cls(env.get("CITI_PG_DSN", ""), env.get("CITI_PG_READER_DSN", ""))


def sanitized(exc):
    """Map a psycopg error to a stable, secret-free message."""
    state = getattr(exc, "sqlstate", None)
    return PostgresOperationError(f"PostgreSQL operation failed ({state})" if state else "PostgreSQL connection or operation failed")
