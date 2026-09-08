"""LLM layer.

A thin `LLMClient` wrapper around the **Portkey** SDK (not the OpenAI SDK).
Every agent in the platform routes its model calls through this single client so
the gateway, provider, model, headers and retry behaviour live in exactly one
place.

The Portkey gateway is OpenAI-shaped, so the client exposes a small, purpose-fit
surface: `complete()` for plain text and `complete_json()` for structured output.
"""
from __future__ import annotations

import json
import re
from typing import Any

from portkey_ai import Portkey

from .config import PORTKEY


class LLMError(RuntimeError):
    """Raised when the gateway call fails or returns unusable content."""


class LLMClient:
    """Wrapper over the Portkey gateway used by all agents."""

    def __init__(self) -> None:
        # `api_key` -> x-portkey-api-key, `provider` -> x-portkey-provider.
        # `base_url` points at the self-hosted Perficient Portkey gateway.
        self._client = Portkey(
            base_url=PORTKEY.base_url,
            api_key=PORTKEY.api_key,
            provider=PORTKEY.provider,
        )
        self.model = PORTKEY.model

    # -- core call ---------------------------------------------------------
    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        max_tokens: int = 2500,
    ) -> str:
        """Return the model's text response for a system+user prompt.

        Note: `temperature` is accepted for call-site symmetry but intentionally
        not forwarded — the Sonnet-5 Bedrock model rejects it as deprecated.
        """
        _ = temperature  # deliberately unused for this model
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as exc:  # noqa: BLE001 - surface a clean error to the API
            raise LLMError(f"Portkey gateway call failed: {exc}") from exc

        try:
            content = resp.choices[0].message.content
        except (AttributeError, IndexError, KeyError) as exc:
            raise LLMError(f"Unexpected gateway response shape: {resp!r}") from exc

        if not content:
            raise LLMError("Gateway returned empty content.")
        return content.strip()

    # -- structured output -------------------------------------------------
    def complete_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4000,
    ) -> Any:
        """Ask the model for JSON and parse it defensively.

        We instruct the model to emit JSON only, then strip any accidental code
        fences / prose before parsing so a stray '```json' never breaks a demo.
        """
        guarded_system = (
            system
            + "\n\nRespond with a single valid JSON value and nothing else. "
            "Do not wrap it in Markdown code fences or add commentary."
        )
        raw = self.complete(
            guarded_system, user, temperature=temperature, max_tokens=max_tokens
        )
        return self._parse_json(raw)

    @staticmethod
    def _parse_json(raw: str) -> Any:
        text = raw.strip()
        # Strip ```json ... ``` fences if the model added them anyway.
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Fall back to the first {...} or [...] span in the text.
            match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError as exc:
                    raise LLMError(
                        f"Could not parse JSON from model output: {raw[:400]}"
                    ) from exc
            raise LLMError(f"Model did not return JSON: {raw[:400]}")


# Single shared instance for the app.
llm = LLMClient()
