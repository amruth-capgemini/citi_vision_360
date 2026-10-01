import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from citi_project.services.agents import (AzureOpenAIJsonModel, AzureOpenAIModelConfig, ModelError,
                                          OpenAIJsonModel, OpenAIModelConfig, select_model)
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


AZURE_ENV = {"AZURE_OPENAI_ENDPOINT": "https://example-resource.openai.azure.com/",
             "AZURE_OPENAI_DEPLOYMENT": "routing-deployment", "AZURE_OPENAI_API_VERSION": "2024-10-21"}


@pytest.fixture
def azure_env(monkeypatch):
    for name, value in AZURE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-placeholder-never-a-real-key")


def test_azure_client_receives_endpoint_version_and_key(azure_env, monkeypatch):
    factory = Mock()
    factory.return_value.chat.completions.create.return_value = completion()
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AzureOpenAI=factory))
    assert AzureOpenAIJsonModel().complete("test_stage", "system", {"data": "value"}, SCHEMA) == {"ok": True}
    kwargs = factory.call_args.kwargs
    assert kwargs["azure_endpoint"] == AZURE_ENV["AZURE_OPENAI_ENDPOINT"]
    assert kwargs["api_version"] == "2024-10-21"
    assert kwargs["api_key"] == "test-placeholder-never-a-real-key"
    assert kwargs["max_retries"] == 0
    args = factory.return_value.chat.completions.create.call_args.kwargs
    assert args["model"] == "routing-deployment"
    assert args["response_format"]["json_schema"]["strict"] is True
    assert args["store"] is False


def test_azure_chat_deployment_alias(azure_env, monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "chat-deployment")
    assert AzureOpenAIModelConfig.from_env().model == "chat-deployment"
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "preferred")
    assert AzureOpenAIModelConfig.from_env().model == "preferred"


@pytest.mark.parametrize("missing", ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"])
def test_azure_missing_configuration(azure_env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    monkeypatch.delenv("AZURE_OPENAI_CHAT_DEPLOYMENT", raising=False)
    with pytest.raises(ModelError, match="unavailable"):
        AzureOpenAIJsonModel()


def test_azure_missing_key_fails_before_client_creation(azure_env, monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY")
    factory = Mock()
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AzureOpenAI=factory))
    with pytest.raises(ModelError, match="AZURE_OPENAI_API_KEY is unavailable"):
        AzureOpenAIJsonModel().complete("test", "prompt", {}, SCHEMA)
    factory.assert_not_called()


@pytest.mark.parametrize("change", [{"endpoint": "http://insecure.openai.azure.com"}, {"endpoint": "https://x/path?q=1"},
                                    {"api_version": "2024-06-01"}, {"api_version": "latest"}, {"model": ""}])
def test_azure_config_rejects_unsafe_or_unsupported_values(change):
    values = {"model": "d", "endpoint": "https://example-resource.openai.azure.com", **change}
    with pytest.raises(ModelError):
        AzureOpenAIModelConfig(**values)


def test_azure_preview_version_accepted():
    assert AzureOpenAIModelConfig(model="d", endpoint="https://r.openai.azure.com", api_version="2024-08-01-preview")


def test_azure_errors_sanitized_and_input_bound(azure_env):
    client = Mock()
    error = RuntimeError("private-secret-placeholder")
    error.status_code = 404
    client.chat.completions.create.side_effect = error
    with pytest.raises(ModelError) as exc:
        AzureOpenAIJsonModel(client=client).complete("test", "prompt", {}, SCHEMA)
    assert str(exc.value) == "Azure OpenAI request failed: model_not_found"
    config = AzureOpenAIModelConfig(model="d", endpoint="https://r.openai.azure.com", max_input_chars=1000)
    with pytest.raises(ModelError, match="bound"):
        AzureOpenAIJsonModel(config, client=client).complete("test", "x" * 2000, {}, SCHEMA)
    assert "placeholder" not in repr(AzureOpenAIJsonModel(client=client).config)


def test_select_model_prefers_azure_when_configured(azure_env, monkeypatch):
    assert isinstance(select_model(), AzureOpenAIJsonModel)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT")
    selected = select_model()
    assert type(selected) is OpenAIJsonModel


def test_select_model_bound_overrides_are_validated(azure_env):
    assert select_model(max_output_tokens=4096, timeout_seconds=90).config.max_output_tokens == 4096
    with pytest.raises(ModelError, match="output bound"):
        select_model(max_output_tokens=100000)
    with pytest.raises(ModelError, match="Unsupported"):
        select_model(model="another-deployment")
