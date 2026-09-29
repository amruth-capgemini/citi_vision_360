from unittest.mock import MagicMock

import pytest
from neo4j.exceptions import ServiceUnavailable

from citi_project.services.knowledge_graph.client import GraphConnectionError, Neo4jClient
from citi_project.services.knowledge_graph.config import GraphConfigurationError, Neo4jConfig


def config():
    return Neo4jConfig("neo4j://localhost:7687", "test-user", "test-password", "test-database")


@pytest.mark.parametrize("missing", ["URI", "USERNAME", "PASSWORD", "DATABASE"])
def test_required_environment(missing):
    values = {"NEO4J_URI": "bolt://localhost", "NEO4J_USERNAME": "test-user",
              "NEO4J_PASSWORD": "test-password", "NEO4J_DATABASE": "test-database"}
    values.pop("NEO4J_" + missing)
    with pytest.raises(GraphConfigurationError, match="NEO4J_" + missing):
        Neo4jConfig.from_env(values)


@pytest.mark.parametrize("uri", ["https://localhost", "neo4j://user:password@localhost", "bolt://", "bolt://localhost:bad", "bolt://localhost/path"])
def test_bad_uri_safe_error(uri):
    with pytest.raises(GraphConfigurationError) as exc:
        Neo4jConfig(uri, "user", "password", "db")
    assert uri not in str(exc.value)
    assert "user:password" not in str(exc.value)


def test_config_repr_hides_all_values():
    value = config()
    assert repr(value) == "Neo4jConfig()"


def test_client_lazy_session_and_close():
    driver = MagicMock()
    factory = MagicMock(return_value=driver)
    session = driver.session.return_value.__enter__.return_value
    session.execute_read.side_effect = lambda callback, *args: callback(MagicMock(), *args)
    with Neo4jClient(config(), driver_factory=factory) as client:
        factory.assert_not_called()
        assert client.read(lambda tx: 7) == 7
        assert client.read(lambda tx: 9) == 9
    factory.assert_called_once()
    driver.session.assert_called_with(database="test-database")
    driver.close.assert_called_once()
    with pytest.raises(GraphConnectionError, match="closed"):
        client.read(lambda tx: None)


def test_client_errors_do_not_expose_driver_message():
    driver = MagicMock()
    driver.session.side_effect = ServiceUnavailable("test-password secret URI")
    with Neo4jClient(config(), driver_factory=lambda *a, **k: driver) as client:
        with pytest.raises(GraphConnectionError) as exc:
            client.check_connectivity()
    assert "test-password" not in str(exc.value)
    assert exc.value.__suppress_context__
