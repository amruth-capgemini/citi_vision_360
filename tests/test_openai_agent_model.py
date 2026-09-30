import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from citi_project.services.agents import ModelError, OpenAIJsonModel, OpenAIModelConfig
from citi_project.services.agents.contracts import obj


SCHEMA = obj({"ok": {"type": "boolean"}})


def completion(content='{"ok":true}', finish="stop", refusal=None):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content, refusal=refusal))])


def test_sdk_request_contract_and_config(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "configured-model")
    client = Mock()
    client.chat.completions.create.return_value = completion()
    model = OpenAIJsonModel(client=client)
    assert model.complete("test_stage", "system", {"data": "value"}, SCHEMA) == {"ok": True}
    args = client.chat.completions.create.call_args.kwargs
    assert args["model"] == "configured-model"
    assert args["response_format"]["json_schema"]["strict"] is True
    assert args["store"] is False
    assert args["max_completion_tokens"] <= 4096
    assert "api_key" not in json.dumps(args)


def test_environment_key_absent_fails_without_client_creation(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ModelError, match="unavailable"):
        OpenAIJsonModel().complete("test", "prompt", {}, SCHEMA)


def test_key_only_passed_to_sdk_official_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder-never-a-real-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://untrusted.invalid")
    factory = Mock()
    factory.return_value.chat.completions.create.return_value = completion()
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=factory))
    OpenAIJsonModel().complete("test", "prompt", {}, SCHEMA)
    assert factory.call_args.kwargs["api_key"] == "test-placeholder-never-a-real-key"
    assert factory.call_args.kwargs["base_url"] == "https://api.openai.com/v1"
    assert factory.call_args.kwargs["max_retries"] == 0


@pytest.mark.parametrize("finish,refusal", [("length", None), ("stop", "refused")])
def test_incomplete_or_refused_response(finish, refusal):
    client = Mock()
    client.chat.completions.create.return_value = completion(finish=finish, refusal=refusal)
    with pytest.raises(ModelError):
        OpenAIJsonModel(client=client).complete("test", "prompt", {}, SCHEMA)


def test_raw_error_and_credentials_not_exposed():
    client = Mock()
    error = RuntimeError("private-secret-placeholder")
    error.status_code = 401
    client.chat.completions.create.side_effect = error
    with pytest.raises(ModelError) as exc:
        OpenAIJsonModel(client=client).complete("test", "prompt", {}, SCHEMA)
    assert str(exc.value) == "OpenAI request failed: authentication"


def test_invalid_json_and_input_bound():
    client = Mock()
    client.chat.completions.create.return_value = completion(content="not-json")
    with pytest.raises(ModelError, match="JSON"):
        OpenAIJsonModel(client=client).complete("test", "prompt", {}, SCHEMA)
    with pytest.raises(ModelError, match="bound"):
        OpenAIJsonModel(OpenAIModelConfig(max_input_chars=1000), client=client).complete("test", "x" * 2000, {}, SCHEMA)
