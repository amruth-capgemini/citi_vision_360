import json
from unittest.mock import patch

import pytest

from citi_project.services.knowledge_graph.cli import main, read_payload
from citi_project.services.knowledge_graph.models import GraphInputError


def test_offline_sample_describe_and_dry_run(tmp_path, capsys):
    with patch("citi_project.services.knowledge_graph.cli.create_client") as client:
        sample = tmp_path / "sample.json"
        assert main(["sample", "--output", str(sample)]) == 0
        assert main(["ingest", str(sample), "--dry-run"]) == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["entities"] == summary["relationships"] == 4
        assert summary["database_checked"] is False
        assert main(["describe"]) == 0
        catalog = json.loads(capsys.readouterr().out)
        assert len(catalog["classes"]) == 36 and len(catalog["relationships"]) == 32
        assert "occurrence_id" in catalog["classes"]["RenewalClause"]["identity_strategy"]
        client.assert_not_called()


@pytest.mark.parametrize("text", ['{"namespace":"a","namespace":"b"}', '{"entities":NaN}', '{', '[]'])
def test_malformed_json_rejected(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises(GraphInputError):
        read_payload(path)


def test_cli_requires_explicit_config_without_leaking_values(monkeypatch, capsys):
    for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE", "NEO4J_TRANSPORT", "NEO4J_QUERY_API_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["check"])
    assert exc.value.code == 2
    assert "NEO4J_URI is required" in capsys.readouterr().err


def test_exact_reconciliation_requires_payload_without_connecting():
    with patch("citi_project.services.knowledge_graph.cli.create_client") as client:
        with pytest.raises(SystemExit):
            main(["validate", "--namespace", "test", "--exact"])
        client.assert_not_called()
