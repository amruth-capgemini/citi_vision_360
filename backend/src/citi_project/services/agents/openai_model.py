"""Lazy SDK boundaries; environment-only keys and sanitized failure categories."""

from dataclasses import dataclass, replace
import json
import logging
import os
import re

from .contracts import ModelError, SPECIALISTS, validate

log = logging.getLogger(__name__)
# Only known API codes are safe to emit; arbitrary error fields may contain user data.
SAFE_ERROR_CODES = frozenset({"invalid_api_key", "insufficient_quota", "rate_limit_exceeded",
    "model_not_found", "invalid_json_schema", "unsupported_parameter", "invalid_parameter",
    "context_length_exceeded", "account_deactivated", "permission_denied", "server_error"})

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
        stage_name = stage if stage in SPECIALISTS or stage in {"supervisor_route", "supervisor_synthesis", "catalog_enrich"} or stage in {f"explore_{name}" for name in SPECIALISTS} else "unknown"
        failing_stage = {"supervisor_route": "routing", "supervisor_synthesis": "synthesis"}.get(
            stage_name, "planning" if stage_name in SPECIALISTS else "exploration" if stage_name.startswith("explore_") else "other")
        diagnostic = {"phase": "input", "category": "request-boundary"}
        try:
            return self._complete(stage, prompt, payload, schema, diagnostic)
        except Exception as exc:
            log.warning("model_failure stage=%s operation=%s provider=%s phase=%s category=%s exception=%s status=%s code=%s finish_reason=%s",
                        failing_stage, stage_name, self.provider, diagnostic["phase"], diagnostic["category"],
                        diagnostic.get("exception", type(exc).__name__), diagnostic.get("status"),
                        diagnostic.get("code"), diagnostic.get("finish_reason"))
            raise

    def _complete(self, stage, prompt, payload, schema, diagnostic):
        content = json.dumps(payload, ensure_ascii=False)
        if len(content) + len(prompt) + len(json.dumps(schema)) > self.config.max_input_chars:
            raise ModelError("Model context exceeds the configured bound")
        diagnostic.update(phase="client", category="configuration")
        client = self._get_client()
        diagnostic.update(phase="request", category="transport")
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
            code = getattr(exc, "code", None)
            diagnostic.update(exception=type(exc).__name__, status=status if type(status) is int else None,
                              code=code if isinstance(code, str) and code in SAFE_ERROR_CODES else None,
                              category={401: "auth", 403: "auth", 429: "rate-limit", 400: "request/model/schema", 404: "model-response"}.get(status, "service" if type(status) is int and status >= 500 else "transport"))
            raise ModelError(f"{self.provider} request failed: {category}") from None
        diagnostic.update(phase="response", category="model-response")
        if response.choices:
            finish = response.choices[0].finish_reason
            diagnostic["finish_reason"] = finish if finish in {"stop", "length", "content_filter", "tool_calls", "function_call"} else "unknown"
        if not response.choices or response.choices[0].finish_reason != "stop" or response.choices[0].message.refusal:
            raise ModelError(f"{self.provider} response refused or incomplete")
        diagnostic.update(phase="json", category="model-response")
        try:
            result = json.loads(response.choices[0].message.content)
        except (TypeError, ValueError) as exc:
            diagnostic["exception"] = type(exc).__name__
            raise ModelError(f"{self.provider} response was not valid JSON") from None
        diagnostic.update(phase="validation", category="schema-validation")
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
