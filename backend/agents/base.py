"""Agent framework.

Every agent subclasses `BaseAgent`, declares an input schema (used to render
the frontend form automatically) and implements `run()`. All LLM access goes
through the shared Portkey-backed `llm` client. A module-level `REGISTRY`
collects the agents the API and UI expose.

Adding an agent
---------------
Subclass `BaseAgent`, set `meta`, implement `run()`, decorate with `@register`,
and import the module in `backend/agents/__init__.py`. That is the whole
contract. Run history, input redaction, timing and error capture come from
`execute()` and need no per-agent code.

Call `execute()`, never `run()`, from routes and the orchestrator. `run()` is
the part an agent author writes; `execute()` is the part that makes it
observable.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

from ..llm import llm  # noqa: F401 - re-exported for agents


@dataclass
class Field:
    """One input field, used to auto-generate the UI form."""

    name: str
    label: str
    type: str = "text"  # text | textarea | select | number | date | file
    placeholder: str = ""
    required: bool = True
    options: list[dict[str, str]] = field(default_factory=list)  # for select
    help: str = ""
    # Bounds for `number`. Declared here rather than left to the UI because an
    # unbounded number input accepts anything the wheel lands on -- including
    # negatives -- and the agent is the thing that knows what range makes
    # sense. `dataclasses.asdict` carries them to the form automatically.
    min: float | None = None
    max: float | None = None
    step: float | None = None


@dataclass
class AgentMeta:
    id: str
    name: str
    tagline: str
    description: str
    icon: str  # a short glyph/emoji for the card
    category: str
    fields: list[Field] = field(default_factory=list)


# -- run-history redaction ----------------------------------------------------
# Agent inputs reach us straight from a multipart form, so they can contain a
# megabyte of scan image; results can contain whole OCR transcripts. Neither
# belongs verbatim in a history table, and raw bytes are not JSON at all.
_MAX_STRING = 4000
_MAX_ITEMS = 200
_MAX_DEPTH = 8


def sanitise_for_storage(value: Any, *, depth: int = 0) -> Any:
    """Coerce an arbitrary agent payload into something JSONB can hold.

    Deliberately total: it never raises, because a failure to record history
    must not fail the agent run that produced it. Unknown types degrade to a
    type name rather than blowing up the insert.
    """
    if depth > _MAX_DEPTH:
        return "<max depth exceeded>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # date/datetime pass through: DateAwareJSONB round-trips them properly.
    if isinstance(value, (date, datetime)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        # An uploaded scan. Keep the fact and the size, never the payload.
        return {"__bytes__": len(value)}
    if isinstance(value, str):
        if len(value) <= _MAX_STRING:
            return value
        # ASCII marker: these strings surface in Windows consoles and log
        # files whose encoding is not reliably UTF-8.
        return value[:_MAX_STRING] + f"... <truncated {len(value) - _MAX_STRING} chars>"
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_ITEMS:
                out["__truncated__"] = f"{len(value) - _MAX_ITEMS} more keys"
                break
            out[str(k)] = sanitise_for_storage(v, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out_list = [sanitise_for_storage(v, depth=depth + 1) for v in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            out_list.append(f"... <{len(items) - _MAX_ITEMS} more items>")
        return out_list
    return f"<{type(value).__name__}>"


def _identifier(inputs: dict[str, Any], *keys: str) -> str | None:
    """First non-empty value among `keys`, so a run is findable by case."""
    for key in keys:
        value = inputs.get(key)
        if value and isinstance(value, str) and value.strip():
            return value.strip()[:32]
    return None


def _warn(what: str, exc: BaseException) -> None:
    print(f"[agent-run] {what}: {type(exc).__name__}: {exc}", file=sys.stderr)


class BaseAgent:
    meta: AgentMeta

    #: Set False on an agent whose invocations are not worth a history row.
    record_runs: bool = True

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        """Do the work. Implemented by every agent; called only by execute()."""
        raise NotImplementedError

    def execute(self, inputs: dict[str, Any], *,
                thread_id: str | None = None) -> dict[str, Any]:
        """Run the agent and record the attempt.

        A history row is inserted before `run()` starts, so a long
        orchestration is visible while it is still going and a crashed process
        leaves evidence rather than nothing.

        Recording is best-effort throughout: if the database is unreachable the
        agent still runs and still returns. Observability failing closed would
        turn a metrics outage into an application outage.
        """
        inputs = inputs or {}
        run_id = self._start_run(inputs, thread_id) if self.record_runs else None
        started = time.monotonic()
        try:
            result = self.run(inputs)
        except BaseException as exc:
            self._finish_run(run_id, started, error=f"{type(exc).__name__}: {exc}")
            raise
        self._finish_run(run_id, started, result=result)
        return result

    # -- history ----------------------------------------------------------
    def _start_run(self, inputs: dict[str, Any], thread_id: str | None) -> int | None:
        try:
            from ..db import session_scope
            from ..db.models import AgentRun

            with session_scope() as session:
                row = AgentRun(
                    agent_id=self.meta.id,
                    case_id=_identifier(inputs, "case_id", "kt_id", "interview_id"),
                    employee_id=_identifier(inputs, "employee_id", "emp_id"),
                    thread_id=thread_id,
                    inputs=sanitise_for_storage(inputs),
                )
                session.add(row)
                session.flush()
                return row.id
        except Exception as exc:  # noqa: BLE001 - never block the agent
            _warn(f"could not open history row for {self.meta.id}", exc)
            return None

    def _finish_run(self, run_id: int | None, started: float, *,
                    result: dict[str, Any] | None = None,
                    error: str | None = None) -> None:
        if run_id is None:
            return
        try:
            from ..db import session_scope
            from ..db.models import AgentRun

            with session_scope() as session:
                row = session.get(AgentRun, run_id)
                if row is None:
                    return
                row.finished_at = datetime.now(timezone.utc)
                row.duration_ms = int((time.monotonic() - started) * 1000)
                row.error = error
                row.result = sanitise_for_storage(result) if result is not None else None
        except Exception as exc:  # noqa: BLE001
            _warn(f"could not close history row {run_id}", exc)


REGISTRY: dict[str, BaseAgent] = {}


def register(agent_cls: Callable[[], BaseAgent]) -> Callable[[], BaseAgent]:
    """Class decorator that instantiates and registers an agent.

    Rejects a duplicate id rather than overwriting: two agents silently
    sharing an id would make one of them unreachable from the UI and
    unattributable in the run history, which is a confusing way to lose an
    agent.
    """
    instance = agent_cls()
    agent_id = instance.meta.id
    existing = REGISTRY.get(agent_id)
    if existing is not None and type(existing) is not agent_cls:
        raise ValueError(
            f"Duplicate agent id {agent_id!r}: already registered by "
            f"{type(existing).__name__}, now also {agent_cls.__name__}."
        )
    REGISTRY[agent_id] = instance
    return agent_cls
