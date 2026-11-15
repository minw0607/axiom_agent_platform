"""Provider adapters: recorded responses (keyless demo), OpenAI and Azure OpenAI (and any
OpenAI-compatible endpoint), Anthropic, and a local Ollama server.

Each adapter reads its own settings from the environment, so one deployment can use several
providers at once: MODEL_ALIAS_MAP decides which alias goes to which. Error messages keep only
the error class, never the deployment or endpoint.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from axiom.router.types import ModelRequest, ModelResponse, ProviderError


def prompt_sha256(request: ModelRequest) -> str:
    body = json.dumps([m.model_dump() for m in request.messages], sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def _recording_path(root: Path, key: str) -> Path:
    if ".." in key or key.startswith("/"):
        raise ValueError(f"bad recording key: {key}")
    return root / f"{key}.json"


class RecordedAdapter:
    """Replays stored responses by recording key. Used in demo mode and in tests.

    A recording made from different prompt text still replays, but is reported with
    prompt_matches=False so drift is visible in the evidence bundle.
    """

    provider = "recorded"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.last_prompt_matches: bool | None = None

    async def complete(self, request: ModelRequest, deployment: str = "") -> ModelResponse:
        path = _recording_path(self.root, request.recording_key)
        if not path.is_file():
            raise ProviderError(f"no recording for {request.recording_key}")
        raw = json.loads(path.read_text())
        self.last_prompt_matches = raw.get("prompt_sha256") == prompt_sha256(request)
        return ModelResponse(
            text=raw["text"],
            input_tokens=int(raw.get("input_tokens", 0)),
            output_tokens=int(raw.get("output_tokens", 0)),
            latency_ms=0,
            origin=raw.get("origin", "recorded"),
        )


def save_recording(root: Path, request: ModelRequest, response: ModelResponse, origin: str) -> Path:
    path = _recording_path(root, request.recording_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "origin": origin,
        "route": request.route,
        "prompt_id": request.prompt_id,
        "prompt_version": request.prompt_version,
        "prompt_sha256": prompt_sha256(request),
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "text": response.text,
    }
    path.write_text(json.dumps(record, indent=2) + "\n")
    return path


def openai_client_from_env() -> Any:
    """Build the async client from the environment.

    OPENAI_API_KEY is required. OPENAI_BASE_URL points at a compatible endpoint or gateway.
    When OPENAI_API_VERSION is set the endpoint is treated as Azure OpenAI. An API management
    gateway can require its own header: OPENAI_APIM_HEADER_NAME and OPENAI_APIM_SUBSCRIPTION_KEY.
    """
    from openai import AsyncAzureOpenAI, AsyncOpenAI

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ProviderError("OPENAI_API_KEY is not set")
    base_url = os.environ.get("OPENAI_BASE_URL") or None
    api_version = os.environ.get("OPENAI_API_VERSION") or None
    name = os.environ.get("OPENAI_APIM_HEADER_NAME")
    value = os.environ.get("OPENAI_APIM_SUBSCRIPTION_KEY")
    headers = {name: value} if name and value else None
    if api_version:
        if not base_url:
            raise ProviderError("OPENAI_API_VERSION is set but OPENAI_BASE_URL is not")
        return AsyncAzureOpenAI(
            api_key=key,
            api_version=api_version,
            azure_endpoint=base_url,
            default_headers=headers,
            max_retries=1,
        )
    return AsyncOpenAI(api_key=key, base_url=base_url, default_headers=headers, max_retries=1)


class OpenAIAdapter:
    """OpenAI or Azure OpenAI chat completions, configured from the environment."""

    provider = "openai"
    # Newer OpenAI models take max_completion_tokens; some compatible servers know only max_tokens.
    token_param = "max_completion_tokens"

    def __init__(self, client: Any = None) -> None:
        self._client = client if client is not None else openai_client_from_env()

    async def complete(self, request: ModelRequest, deployment: str) -> ModelResponse:
        import openai

        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": deployment,
            "messages": [m.model_dump() for m in request.messages],
            self.token_param: request.max_output_tokens,
        }
        if request.json_output:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            result = await self._client.chat.completions.create(**kwargs)
        except openai.APIError as exc:
            # The message can name the deployment; keep only the error class.
            raise ProviderError(f"{self.provider} call failed: {type(exc).__name__}") from None
        choice = result.choices[0]
        text = choice.message.content or ""
        usage = result.usage
        return ModelResponse(
            text=text,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            origin="live",
        )


def ollama_client_from_env() -> Any:
    """A client for a local Ollama server's OpenAI-compatible endpoint (OLLAMA_BASE_URL)."""
    from openai import AsyncOpenAI

    base_url = (os.environ.get("OLLAMA_BASE_URL") or "").rstrip("/")
    if not base_url:
        raise ProviderError("OLLAMA_BASE_URL is not set")
    # Ollama ignores the key, but the client requires one.
    return AsyncOpenAI(api_key="ollama", base_url=f"{base_url}/v1", max_retries=1)


class OllamaAdapter(OpenAIAdapter):
    """A model served by Ollama on this machine or network: the only provider the router
    treats as local, so the only one allowed to see unredacted personal data."""

    provider = "ollama"
    token_param = "max_tokens"

    def __init__(self, client: Any = None) -> None:
        super().__init__(client if client is not None else ollama_client_from_env())


# Current Claude models think before answering, and thinking counts against max_tokens, so the
# request's output budget gets this much room on top.
ANTHROPIC_MIN_MAX_TOKENS = 16000
_JSON_ONLY = "Respond with a single JSON object and nothing else: no prose, no code fences."


def anthropic_client_from_env() -> Any:
    """ANTHROPIC_API_KEY is required. ANTHROPIC_BASE_URL optionally points at a gateway."""
    from anthropic import AsyncAnthropic

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ProviderError("ANTHROPIC_API_KEY is not set")
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
    return AsyncAnthropic(api_key=key, base_url=base_url, max_retries=1)


def _strip_fences(text: str) -> str:
    body = text.strip()
    if body.startswith("```") and body.endswith("```"):
        body = body[3:-3]
        if body.startswith("json"):
            body = body[4:]
    return body.strip()


class AnthropicAdapter:
    """Claude through the Anthropic Messages API, configured from the environment."""

    provider = "anthropic"

    def __init__(self, client: Any = None) -> None:
        self._client = client if client is not None else anthropic_client_from_env()

    async def complete(self, request: ModelRequest, deployment: str) -> ModelResponse:
        import anthropic

        started = time.perf_counter()
        system = [m.content for m in request.messages if m.role == "system"]
        if request.json_output:
            system.append(_JSON_ONLY)
        kwargs: dict[str, Any] = {
            "model": deployment,
            "max_tokens": max(request.max_output_tokens, ANTHROPIC_MIN_MAX_TOKENS),
            "messages": [
                {"role": m.role, "content": m.content}
                for m in request.messages
                if m.role != "system"
            ],
        }
        if system:
            kwargs["system"] = "\n\n".join(system)
        try:
            result = await self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            # The message can name the model; keep only the error class.
            raise ProviderError(f"anthropic call failed: {type(exc).__name__}") from None
        if result.stop_reason == "refusal":
            raise ProviderError("anthropic call failed: the model declined")
        if result.stop_reason == "max_tokens":
            raise ProviderError("anthropic call failed: output cut off at max_tokens")
        text = "".join(block.text for block in result.content if block.type == "text")
        return ModelResponse(
            text=_strip_fences(text) if request.json_output else text,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
            origin="live",
        )
