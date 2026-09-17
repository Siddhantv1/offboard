"""LangGraph-based multi-agent orchestration.

Agents 1-10 remain plain single-shot `BaseAgent`s (see `backend/agents/`) invoked
directly from the API. This package adds a second, graph-shaped execution mode
for work that isn't a single LLM call: fan-out to several agents, a join, and a
bounded retry/reroute cycle when a clearance is blocked. It calls the existing
agents through `REGISTRY` unchanged -- no agent code needed to change for this.
"""
from .graph import (pending_approval, pending_approvals, run_orchestration,
                    stream_orchestration)

__all__ = ["pending_approval", "pending_approvals", "run_orchestration",
           "stream_orchestration"]
