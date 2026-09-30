"""Explicit environment configuration; no dotenv loading or credential defaults."""

from dataclasses import dataclass, field
import os
from urllib.parse import quote, urlsplit


class GraphConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    database: str = field(repr=False)
    transport: str = field(default="bolt", repr=False)
    query_api_url: str = field(default="", repr=False)

    def __post_init__(self):
        if self.transport not in ("bolt", "http"):
            raise GraphConfigurationError("NEO4J_TRANSPORT must be bolt or http")
        required = ("uri", "username", "password", "database") if self.transport == "bolt" else ("username", "password", "database")
        for name in required:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise GraphConfigurationError(f"NEO4J_{name.upper()} is required")
        try:
            uri = urlsplit(self.uri)
            valid = (not any(c.isspace() for c in self.uri)
                     and uri.scheme in ("neo4j", "neo4j+s", "neo4j+ssc", "bolt", "bolt+s", "bolt+ssc")
                     and uri.hostname and uri.username is None and uri.password is None
                     and not uri.fragment and not uri.query and uri.path in ("", "/"))
            uri.port  # validate the port without including it in diagnostics
        except ValueError:
            valid = False
        if not valid and (self.transport == "bolt" or self.uri):
            raise GraphConfigurationError("NEO4J_URI must be a supported Bolt/routing URI without embedded credentials")
        if self.transport == "http":
            if ":" in self.username:
                raise GraphConfigurationError("NEO4J_USERNAME cannot contain a colon for Basic authentication")
            self.http_endpoint  # validate before any network activity

    @property
    def http_endpoint(self):
        base = self.query_api_url
        if not base:
            host = urlsplit(self.uri).hostname
            if not host or not host.endswith(".databases.neo4j.io"):
                raise GraphConfigurationError("NEO4J_QUERY_API_URL is required unless NEO4J_URI identifies an Aura hostname")
            base = "https://" + host
        try:
            url = urlsplit(base)
            valid = (url.scheme == "https" and url.hostname and not url.username and not url.password
                     and not url.query and not url.fragment and url.path in ("", "/")
                     and not any(c.isspace() or ord(c) < 32 for c in base) and "\\" not in base)
            url.port
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise GraphConfigurationError("NEO4J_QUERY_API_URL must be an HTTPS base URL without credentials, path, query or fragment")
        if self.database in (".", ".."):
            raise GraphConfigurationError("NEO4J_DATABASE is invalid")
        return base.rstrip("/") + "/db/" + quote(self.database, safe="") + "/query/v2"

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        return cls(*(env.get(f"NEO4J_{name}", "") for name in ("URI", "USERNAME", "PASSWORD", "DATABASE")),
                   transport=env.get("NEO4J_TRANSPORT", "bolt"), query_api_url=env.get("NEO4J_QUERY_API_URL", ""))
