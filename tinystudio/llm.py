"""OpenAI-compatible chat client for the steps that need a helper model:
synthetic generation, augmentation, rule generation, and judging.

Point it at anything that speaks ``/v1/chat/completions`` and that you run
yourself: ``mlx_lm.server`` (Apple Silicon), ``llama-server``, Ollama, vLLM,
LM Studio, or tinystudio's own ``serve`` command. No key is required unless
your server wants one.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx


class LLMError(RuntimeError):
    """The helper model could not be reached or answered unusably."""


@dataclass(frozen=True)
class LLMClient:
    base_url: str
    model: str
    api_key: str | None = None
    timeout: float = 180.0

    @classmethod
    def from_env(cls, base_url: str | None = None, model: str | None = None) -> "LLMClient":
        base = base_url or os.environ.get("TINYSTUDIO_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
        name = model or os.environ.get("TINYSTUDIO_LLM_MODEL", "")
        return cls(base.rstrip("/"), name, os.environ.get("TINYSTUDIO_LLM_API_KEY"))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def resolve_model(self) -> str:
        """Use the configured model, or the single model the server lists."""
        if self.model:
            return self.model
        try:
            response = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=20)
            response.raise_for_status()
            data = response.json().get("data", [])
        except (httpx.HTTPError, ValueError) as cause:
            raise LLMError(f"no model configured and {self.base_url}/models is unreachable: {cause}") from cause
        if len(data) != 1:
            names = ", ".join(str(item.get("id")) for item in data) or "none"
            raise LLMError(f"set TINYSTUDIO_LLM_MODEL; the server lists {len(data)} models ({names})")
        return str(data[0]["id"])

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.2, max_tokens: int = 1024, json_mode: bool = False) -> str:
        body: dict[str, Any] = {
            "model": self.resolve_model(),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            response = httpx.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            return str(payload["choices"][0]["message"]["content"])
        except httpx.HTTPStatusError as cause:
            if json_mode and cause.response.status_code == 400:
                return self.chat(messages, temperature, max_tokens, json_mode=False)
            raise LLMError(f"{self.base_url}: HTTP {cause.response.status_code} {cause.response.text[:300]}") from cause
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as cause:
            raise LLMError(f"{self.base_url}: {cause}") from cause

    def chat_json(self, messages: list[dict[str, str]], temperature: float = 0.1, max_tokens: int = 2048) -> Any:
        text = self.chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)
        return extract_json(text)


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply that may wrap it in prose or a code fence."""
    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    candidates = [fence.group(1) if fence else text]
    first = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if first >= 0:
        candidates.append(text[first:])
    for candidate in candidates:
        candidate = candidate.strip()
        for end in range(len(candidate), 0, -1):
            if candidate[end - 1] not in "}]":
                continue
            try:
                return json.loads(candidate[:end])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"the model did not return JSON: {text[:200]!r}")
