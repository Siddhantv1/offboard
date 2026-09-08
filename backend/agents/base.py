"""Agent framework.

Every agent subclasses `BaseAgent`, declares an input schema (used to render the
frontend form automatically) and implements `run()`. All LLM access goes through
the shared Portkey-backed `llm` client. A module-level `REGISTRY` collects the
agents the API and UI expose.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..llm import llm  # noqa: F401 - re-exported for agents


@dataclass
class Field:
    """One input field, used to auto-generate the UI form."""

    name: str
    label: str
    type: str = "text"  # text | textarea | select | number
    placeholder: str = ""
    required: bool = True
    options: list[dict[str, str]] = field(default_factory=list)  # for select
    help: str = ""


@dataclass
class AgentMeta:
    id: str
    name: str
    tagline: str
    description: str
    icon: str  # a short glyph/emoji for the card
    category: str
    fields: list[Field] = field(default_factory=list)


class BaseAgent:
    meta: AgentMeta

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


REGISTRY: dict[str, BaseAgent] = {}


def register(agent_cls: Callable[[], BaseAgent]) -> Callable[[], BaseAgent]:
    """Class decorator that instantiates and registers an agent."""
    instance = agent_cls()
    REGISTRY[instance.meta.id] = instance
    return agent_cls
