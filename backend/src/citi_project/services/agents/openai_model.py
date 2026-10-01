"""Lazy SDK boundaries; environment-only keys and sanitized failure categories."""

from dataclasses import dataclass, replace
import json
import os
import re

from .contracts import ModelError, validate

AZURE_API_VERSION = "2024-10-21"
# Strict json_schema response formats require this Azure API version or later.
AZURE_MIN_API_VERSION = "2024-08-01"


@dataclass(frozen=True)
class OpenAIModelConfig:
    model: str = "gpt-4.1-nano-2025-04-14"
    max_output_tokens: int = 1800
    timeout_seconds: float = 30
    max_input_chars: int = 60000

    @classmethod
    def from_env(cls):
        return cls(model=os.environ.get("OPENAI_MODEL") or cls.model)

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 100:
            raise ModelError("Invalid model configuration")
        if type(self.max_output_tokens) is not int or not 128 <= self.max_output_tokens <= 4096:
            raise ModelError("Invalid output bound")
        if not 1 <= self.timeout_seconds <= 120 or not 1000 <= self.max_input_chars <= 120000:
            raise ModelError("Invalid model request bound")


@dataclass(frozen=True)
class AzureOpenAIModelConfig(OpenAIModelConfig):
    """`model` holds the Azure deployment name, which the SDK sends as the model."""
    endpoint: str = ""
    api_version: str = AZURE_API_VERSION

    @classmethod
    def from_env(cls):
        # AZURE_OPENAI_CHAT_DEPLOYMENT is the name some environments use for the same chat deployment.
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT")
        if not endpoint or not deployment:
            raise ModelError("Azure OpenAI endpoint or deployment is unavailable in the environment")
        return cls(model=deployment, endpoint=endpoint,
                   api_version=os.environ.get("AZURE_OPENAI_API_VERSION") or AZURE_API_VERSION)

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.endpoint, str) or not re.fullmatch(r"https://[A-Za-z0-9.-]+/?", self.endpoint):
            raise ModelError("Invalid Azure OpenAI endpoint")
        version = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(-preview)?", self.api_version or "")
        if not version or version.group(1) < AZURE_MIN_API_VERSION:
            raise ModelError("Azure OpenAI API version must support structured outputs")


class OpenAIJsonModel:
    provider, key_env = "OpenAI", "OPENAI_API_KEY"

    def __init__(self, config=None, *, client=None):
        self.config = config or OpenAIModelConfig.from_env()
        self._client = client

    def _create_client(self, key):
        from openai import OpenAI
        # Explicit official endpoint prevents ambient base-URL redirection.
        return OpenAI(api_key=key, base_url="https://api.openai.com/v1",
                      timeout=self.config.timeout_seconds, max_retries=0)

    def _get_client(self):
        if self._client is None:
            key = os.environ.get(self.key_env)
            if not key:
                raise ModelError(f"{self.key_env} is unavailable in the environment")
            try:
                self._client = self._create_client(key)
            except ImportError:
                raise ModelError("OpenAI SDK is not installed") from None
        return self._client

    def complete(self, stage, prompt, payload, schema):
        content = json.dumps(payload, ensure_ascii=False)
        if len(content) + len(prompt) + len(json.dumps(schema)) > self.config.max_input_chars:
            raise ModelError("Model context exceeds the configured bound")
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": content}],
                response_format={"type": "json_schema", "json_schema": {"name": stage, "strict": True, "schema": schema}},
                max_completion_tokens=self.config.max_output_tokens, store=False,
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            category = {401: "authentication", 403: "permission", 429: "rate_or_quota", 400: "request_or_model", 404: "model_not_found"}.get(status, "connection_or_service")
            raise ModelError(f"{self.provider} request failed: {category}") from None
        if not response.choices or response.choices[0].finish_reason != "stop" or response.choices[0].message.refusal:
            raise ModelError(f"{self.provider} response refused or incomplete")
        try:
            result = json.loads(response.choices[0].message.content)
        except (TypeError, ValueError):
            raise ModelError(f"{self.provider} response was not valid JSON") from None
        return validate(result, schema)

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None


class AzureOpenAIJsonModel(OpenAIJsonModel):
    """Same strict request contract as OpenAIJsonModel, sent to an Azure deployment."""
    provider, key_env = "Azure OpenAI", "AZURE_OPENAI_API_KEY"

    def __init__(self, config=None, *, client=None):
        super().__init__(config or AzureOpenAIModelConfig.from_env(), client=client)

    def _create_client(self, key):
        from openai import AzureOpenAI
        return AzureOpenAI(api_key=key, azure_endpoint=self.config.endpoint, api_version=self.config.api_version,
                           timeout=self.config.timeout_seconds, max_retries=0)


def select_model(**bounds):
    """Azure when its endpoint is configured in the environment, otherwise direct OpenAI.

    ``bounds`` override request limits (max_output_tokens, timeout_seconds, max_input_chars)
    and are validated by the config like any other value.
    """
    unknown = set(bounds) - {"max_output_tokens", "timeout_seconds", "max_input_chars"}
    if unknown:
        raise ModelError("Unsupported model bound override")
    if os.environ.get("AZURE_OPENAI_ENDPOINT"):
        return AzureOpenAIJsonModel(replace(AzureOpenAIModelConfig.from_env(), **bounds))
    return OpenAIJsonModel(replace(OpenAIModelConfig.from_env(), **bounds))
