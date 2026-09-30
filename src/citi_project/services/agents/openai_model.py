"""One lazy SDK boundary; environment-only key and sanitized failure categories."""

from dataclasses import dataclass
import json
import os

from .contracts import ModelError, validate


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


class OpenAIJsonModel:
    def __init__(self, config=None, *, client=None):
        self.config = config or OpenAIModelConfig.from_env()
        self._client = client

    def _get_client(self):
        if self._client is None:
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise ModelError("OPENAI_API_KEY is unavailable in the environment")
            try:
                from openai import OpenAI
            except ImportError:
                raise ModelError("OpenAI SDK is not installed") from None
            # Explicit official endpoint prevents ambient base-URL redirection.
            self._client = OpenAI(api_key=key, base_url="https://api.openai.com/v1",
                                  timeout=self.config.timeout_seconds, max_retries=0)
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
            raise ModelError(f"OpenAI request failed: {category}") from None
        if not response.choices or response.choices[0].finish_reason != "stop" or response.choices[0].message.refusal:
            raise ModelError("OpenAI response refused or incomplete")
        try:
            result = json.loads(response.choices[0].message.content)
        except (TypeError, ValueError):
            raise ModelError("OpenAI response was not valid JSON") from None
        return validate(result, schema)

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None
