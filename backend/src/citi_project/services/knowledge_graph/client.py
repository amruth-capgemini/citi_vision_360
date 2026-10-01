"""A reusable lazy driver with managed transactions and sanitized error boundaries."""

from threading import Lock

from neo4j import GraphDatabase, unit_of_work
from neo4j.exceptions import DriverError, Neo4jError

from .config import Neo4jConfig


class GraphConnectionError(RuntimeError):
    pass


class Neo4jClient:
    def __init__(self, config: Neo4jConfig, *, driver_factory=None):
        self.config = config
        self._factory = driver_factory or GraphDatabase.driver
        self._driver = None
        self._closed = False
        self._driver_lock = Lock()

    def _get_driver(self):
        with self._driver_lock:
            if self._closed:
                raise GraphConnectionError("Neo4j client is closed")
            if self._driver is None:
                try:
                    self._driver = self._factory(
                        self.config.uri, auth=(self.config.username, self.config.password),
                        connection_timeout=10, connection_acquisition_timeout=30,
                        max_transaction_retry_time=15,
                    )
                except (DriverError, Neo4jError, ValueError, OSError):
                    raise GraphConnectionError("Unable to initialize Neo4j driver; check configuration") from None
            return self._driver

    def _execute(self, method, callback, *args):
        try:
            with self._get_driver().session(database=self.config.database) as session:
                return getattr(session, method)(unit_of_work(timeout=60)(callback), *args)
        except (DriverError, Neo4jError, OSError):
            # Do not include driver messages, URIs, query parameters or credentials.
            raise GraphConnectionError("Neo4j operation failed; check connectivity, permissions and server compatibility") from None

    def read(self, callback, *args):
        return self._execute("execute_read", callback, *args)

    def write(self, callback, *args):
        return self._execute("execute_write", callback, *args)

    def check_connectivity(self):
        # Checks configured database access, not only the driver's default database.
        return self.read(lambda tx: tx.run("RETURN 1 AS ok").single()["ok"] == 1)

    def close(self):
        try:
            if self._driver is not None:
                self._driver.close()
        except (DriverError, Neo4jError, OSError):
            raise GraphConnectionError("Unable to close Neo4j driver cleanly") from None
        finally:
            self._driver = None
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
