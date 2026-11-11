"""Provider adapters: recorded responses (keyless demo) and OpenAI-compatible APIs."""

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

    def __init__(self) -> None:
        self._client = openai_client_from_env()

    async def complete(self, request: ModelRequest, deployment: str) -> ModelResponse:
        import openai

        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": deployment,
            "messages": [m.model_dump() for m in request.messages],
            "max_completion_tokens": request.max_output_tokens,
        }
        if request.json_output:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            result = await self._client.chat.completions.create(**kwargs)
        except openai.APIError as exc:
            # The message can name the deployment; keep only the error class.
            raise ProviderError(f"openai call failed: {type(exc).__name__}") from None
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
