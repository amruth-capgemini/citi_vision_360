"""Minimal transport contract shared by Bolt and the HTTPS Query API."""

from typing import Any, Callable, Iterator, Protocol, TypeVar

T = TypeVar("T")


class GraphResult(Protocol):
    def __iter__(self) -> Iterator[dict[str, Any]]: ...
    def single(self) -> dict[str, Any] | None: ...
    def consume(self) -> Any: ...


class GraphTransaction(Protocol):
    def run(self, query: str, **parameters: Any) -> GraphResult: ...


class GraphClient(Protocol):
    def read(self, callback: Callable[..., T], *args: Any) -> T: ...
    def write(self, callback: Callable[..., T], *args: Any) -> T: ...
    def check_connectivity(self) -> bool: ...
    def close(self) -> None: ...


def create_client(config):
    """Construct lazily; transport selection never attempts connectivity."""
    if config.transport == "http":
        from .http_client import QueryAPIClient
        return QueryAPIClient(config)
    from .client import Neo4jClient
    return Neo4jClient(config)
