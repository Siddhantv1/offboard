"""LLM layer.

A thin `LLMClient` wrapper around the **Portkey** SDK (not the OpenAI SDK).
Every agent in the platform routes its model calls through this single client so
the gateway, provider, model, headers and retry behaviour live in exactly one
place.

The Portkey gateway is OpenAI-shaped, so the client exposes a small, purpose-fit
surface: `complete()` for plain text, `complete_json()` for structured output,
and `complete_with_tools()` for an agentic tool-use loop.

**Provider portability.** The gateway forwards each upstream provider's own
quirks rather than normalising them, so nothing here keys off a provider name:

  - Structured output uses the provider's JSON mode when it has one. Support is
    negotiated at runtime on the first call and cached, so moving between
    Gemini/OpenAI (which have `response_format`) and Anthropic (which does not)
    needs no config change.
  - Truncation is detected by inspecting the text that arrived, not by matching
    the provider's word for it ("length" / "max_tokens" / "MAX_TOKENS" / the next
    one someone invents). `finish_reason` is only ever a hint.
  - Retries size themselves from the tokens the model actually spent, because
    reasoning models expand their thinking to fill whatever budget you give them
    -- doubling the cap buys more thinking, not more answer.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from portkey_ai import Portkey

from .config import PORTKEY


class LLMError(RuntimeError):
    """Raised when the gateway call fails or returns unusable content."""


# Only a hint -- see `_unterminated()` for the authoritative check.
_TRUNCATION_HINTS = {"length", "max_tokens", "token_limit", "output_limit"}

# Ceiling on a retry, so a runaway response can't bill unbounded tokens.
MAX_OUTPUT_TOKENS = 16000

_UNPARSED = object()  # sentinel: "this text is not JSON", distinct from null


def _hints_truncated(finish_reason: str) -> bool:
    return finish_reason.casefold() in _TRUNCATION_HINTS


def _walk_json(text: str) -> tuple[list[str], bool, int]:
    """Scan `text` as JSON -> (unclosed brackets, ended inside a string, safe cut).

    `safe cut` is the offset just past the last point where the value was
    structurally complete -- a comma or a closing bracket. Cutting there can
    never strand a dangling key or a half-written literal.
    """
    closers: list[str] = []
    in_string = escaped = False
    safe = 0
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            closers.append("}")
        elif ch == "[":
            closers.append("]")
        elif ch in "}]":
            if closers:
                closers.pop()
            safe = i + 1
        elif ch == ",":
            safe = i
    return closers, in_string, safe


def _unterminated(text: str) -> bool:
    """True when `text` stops mid-value -- the provider-independent signature of
    a response that was cut off."""
    closers, in_string, _ = _walk_json(text)
    return bool(closers) or in_string


def _repair_json(text: str) -> str | None:
    """Close an unterminated JSON value, discarding the incomplete tail.

    Returns None when the text is already balanced, or when nothing complete
    enough to salvage was received.
    """
    closers, in_string, safe = _walk_json(text)
    if not closers and not in_string:
        return None
    head = text[:safe].rstrip().rstrip(",").rstrip()
    if not head:
        return None
    remaining, still_open_string, _ = _walk_json(head)
    if still_open_string:
        return None
    return head + "".join(reversed(remaining))


def _retry_budget(max_tokens: int, reasoning_tokens: int | None) -> int:
    """How much room to give a second attempt.

    A reasoning model spends thinking tokens out of the same output budget and
    scales them to fit, so `max_tokens * 2` mostly buys more thinking. When the
    provider reports what it spent thinking, reserve that much again *plus* room
    for the answer; otherwise take one generous step up.
    """
    if reasoning_tokens:
        return min(reasoning_tokens + max(max_tokens, 4000), MAX_OUTPUT_TOKENS)
    return min(max_tokens * 3, MAX_OUTPUT_TOKENS)


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
        # None = not yet probed. Set on the first complete_json() call.
        self._json_mode: bool | None = None

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

        Note: `temperature` is accepted for call-site symmetry but not
        forwarded -- at least one model behind this gateway rejects it outright.
        """
        _ = temperature
        content, _finish, _reasoning = self._chat(system, user, max_tokens)
        return content

    def _chat(
        self, system: str, user: str, max_tokens: int, *, json_mode: bool = False
    ) -> tuple[str, str, int | None]:
        """One chat completion -> (content, finish_reason, reasoning_tokens)."""
        kwargs: dict[str, Any] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - surface a clean error to the API
            raise LLMError(f"Portkey gateway call failed: {exc}") from exc

        try:
            choice = resp.choices[0]
            content = choice.message.content
        except (AttributeError, IndexError, KeyError) as exc:
            raise LLMError(f"Unexpected gateway response shape: {resp!r}") from exc

        if not content:
            raise LLMError("Gateway returned empty content.")
        return (
            content.strip(),
            str(getattr(choice, "finish_reason", "") or ""),
            _reasoning_tokens(resp),
        )

    # -- tool use ----------------------------------------------------------
    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        handlers: dict[str, Callable[..., Any]],
        *,
        max_rounds: int = 8,
        max_tokens: int = 2500,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Run an agentic tool-use loop and return (final_text, trace).

        `tools` is the OpenAI-style function list the gateway forwards upstream;
        `handlers` maps tool name -> Python callable taking the parsed arguments
        as kwargs. Each round: the model either answers (we return) or emits
        tool_calls (we run them, append results, and go again). The trace records
        every call so the UI can show which systems were hit.

        Branches on `tool_calls` being present rather than on `finish_reason`,
        since providers name that stop reason differently ("tool_use",
        "TOOL_CALLS", "tool_calls").
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        trace: list[dict[str, Any]] = []
        for _ in range(max_rounds):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model, max_tokens=max_tokens, tools=tools, messages=messages,
                )
                choice = resp.choices[0]
                msg = choice.message
            except Exception as exc:  # noqa: BLE001
                raise LLMError(f"Portkey gateway call failed: {exc}") from exc

            tool_calls = list(getattr(msg, "tool_calls", None) or [])
            if not tool_calls:
                content = (msg.content or "").strip()
                if not content:
                    raise LLMError("Gateway returned empty content after tool use.")
                return content, trace

            # Echo the assistant turn (with its tool_calls) so the next request
            # is a valid continuation, then one tool message per call.
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                    for tc in tool_calls
                ],
            })
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                handler = handlers.get(name)
                if handler is None:
                    result: Any = {"error": f"Unknown tool '{name}'."}
                else:
                    try:
                        result = handler(**args)
                    except Exception as exc:  # noqa: BLE001 - report to the model, don't crash
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                trace.append({"tool": name, "arguments": args, "result": result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })
        raise LLMError(f"Tool-use loop did not finish within {max_rounds} rounds.")

    # -- structured output -------------------------------------------------
    def complete_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4000,
    ) -> Any:
        """Ask the model for JSON and return it parsed.

        Three lines of defence, in order: the provider's own JSON mode when it
        has one; a retry sized from the tokens the first attempt actually spent;
        and finally repairing a cut-off value rather than losing the whole run.
        A repaired result carries `"_truncated": True` so callers and the UI can
        say the response was cut short instead of presenting it as complete.
        """
        _ = temperature  # see complete(): not forwarded
        guarded = (
            system
            + "\n\nRespond with a single valid JSON value and nothing else. "
            "Do not wrap it in Markdown code fences or add commentary. "
            "Keep string values concise so the whole response fits comfortably."
        )

        raw, finish, reasoning = self._json_call(guarded, user, max_tokens)
        parsed = self._try_parse(raw)
        if parsed is not _UNPARSED:
            return parsed

        if not (_unterminated(raw) or _hints_truncated(finish)):
            raise LLMError(f"Model did not return JSON: {raw[:400]}")

        budget = _retry_budget(max_tokens, reasoning)
        if budget > max_tokens:
            retry_raw, _finish, _reasoning = self._json_call(guarded, user, budget)
            parsed = self._try_parse(retry_raw)
            if parsed is not _UNPARSED:
                return parsed
            if len(retry_raw) > len(raw):
                raw = retry_raw

        salvaged = self._salvage(raw)
        if salvaged is not None:
            return salvaged
        raise LLMError(f"Model output was truncated and unrecoverable: {raw[:400]}")

    def _json_call(
        self, system: str, user: str, max_tokens: int
    ) -> tuple[str, str, int | None]:
        """A chat call that uses the provider's JSON mode when it supports one.

        Support is discovered by trying it: if the call fails with JSON mode on
        but succeeds without, this provider doesn't have it and we stop asking
        for the rest of the process. A transient failure doesn't poison the
        cache, because only a *successful* fallback records the capability.
        """
        if self._json_mode is False:
            return self._chat(system, user, max_tokens)
        try:
            result = self._chat(system, user, max_tokens, json_mode=True)
        except LLMError:
            if self._json_mode is True:
                raise  # known-good provider; this is a real failure
            result = self._chat(system, user, max_tokens)
            self._json_mode = False
            return result
        self._json_mode = True
        return result

    @classmethod
    def _try_parse(cls, raw: str) -> Any:
        """Parse `raw` as JSON, returning `_UNPARSED` if it isn't."""
        text = cls._strip_fence(raw)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Some models still wrap the value in prose; take the first JSON span.
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return _UNPARSED

    @classmethod
    def _salvage(cls, raw: str) -> Any | None:
        """Recover a usable value from a cut-off response, flagged as partial."""
        repaired = _repair_json(cls._strip_fence(raw))
        if repaired is None:
            return None
        try:
            value = json.loads(repaired)
        except json.JSONDecodeError:
            return None
        if isinstance(value, dict):
            value["_truncated"] = True
        elif isinstance(value, list):
            return {"_truncated": True, "items": value}
        return value

    @staticmethod
    def _strip_fence(raw: str) -> str:
        """Drop a ```json fence. Tolerates a missing closing fence, which is
        exactly what a truncated fenced response looks like."""
        text = raw.strip()
        if not text.startswith("```"):
            return text
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rstrip()
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    @classmethod
    def _parse_json(cls, raw: str) -> Any:
        """Strict parse: used by callers that hand us text from another path."""
        parsed = cls._try_parse(raw)
        if parsed is _UNPARSED:
            raise LLMError(f"Model did not return JSON: {raw[:400]}")
        return parsed


def _reasoning_tokens(resp: Any) -> int | None:
    """Thinking tokens the provider reports spending, when it reports them."""
    details = getattr(getattr(resp, "usage", None), "completion_tokens_details", None)
    value = getattr(details, "reasoning_tokens", None) if details else None
    return int(value) if value else None


# Single shared instance for the app.
llm = LLMClient()
