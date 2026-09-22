"""Agent 15 — Exit Interview Trend Analyst (multi-turn).

Longitudinal analysis over *all* exit interviews rather than one: groups them
by department and quarter, tallies sentiment and recurring themes in code, and
has the LLM identify emerging patterns (e.g. rising complaints in a department)
and raise alerts. It is conversational: the first turn (no question) produces
the trend report; follow-up questions carry the conversation history so the
analyst can drill into a department or theme.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ..data_store import store

SYSTEM = """You are a people-analytics trend analyst reviewing exit interviews \
over time. You are given aggregate signals computed from every interview plus \
the interview texts. Identify longitudinal patterns: themes that recur or grow \
within a department or period, sentiment shifts, and manager- or team-specific \
signals. Be evidence-based and cite which interviews support each pattern. \
Raise an alert only when a pattern repeats (2+ interviews) or is severe.

Write your analysis in Markdown (headings, short bullets). Then, on the last \
line, output a JSON object on its own line prefixed with `ALERTS:` in this shape:
ALERTS: {"alerts": [{"department": str, "signal": str, "severity": "high|medium|low", "evidence": [interview ids]}]}
For follow-up questions, answer the question in Markdown and still end with the ALERTS line (it may be empty)."""

THEME_LEXICON = {
    "quota / targets": ("quota", "target"),
    "management / leadership": ("manager", "management", "leadership"),
    "burnout / workload": ("burnout", "pressure", "overwork", "stretched", "relentless"),
    "career growth": ("growth", "promot", "career"),
    "compensation": ("commission", "compensation", "salary", "pay"),
    "coaching / feedback": ("coaching", "feedback", "one-on-one", "1:1"),
    "tooling / process": ("tooling", "process", "documentation"),
    "positive culture": ("supportive", "great", "enjoyed", "friendly", "learned"),
}


def _quarter(date_str: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})", str(date_str))
    if not m:
        return "unknown"
    return f"{m.group(1)}-Q{(int(m.group(2)) - 1) // 3 + 1}"


def _signals() -> dict[str, Any]:
    by_dept: dict[str, list[str]] = defaultdict(list)
    by_quarter: dict[str, list[str]] = defaultdict(list)
    sentiment_by_dept: dict[str, Counter[str]] = defaultdict(Counter)
    themes_by_dept: dict[str, Counter[str]] = defaultdict(Counter)
    recommend_by_dept: dict[str, Counter[str]] = defaultdict(Counter)
    managers: dict[str, Counter[str]] = defaultdict(Counter)
    timeline: list[dict[str, Any]] = []

    for iv in store.all_interviews():
        meta = iv.meta
        iid = str(meta.get("interview_id", iv.slug))
        dept = str(meta.get("department") or "Unknown")
        q = _quarter(meta.get("submitted_date", ""))
        sentiment = str(meta.get("sentiment", "unknown"))
        by_dept[dept].append(iid)
        by_quarter[q].append(iid)
        sentiment_by_dept[dept][sentiment] += 1
        recommend_by_dept[dept][str(meta.get("would_recommend", "unknown")).lower()] += 1
        text = iv.body.lower()
        for theme, words in THEME_LEXICON.items():
            if any(w in text for w in words):
                themes_by_dept[dept][theme] += 1
        emp = store.employee(str(meta.get("employee_id", "")))
        if emp and emp.meta.get("manager"):
            managers[dept][str(emp.meta["manager"])] += 1
        timeline.append({"interview_id": iid, "department": dept, "quarter": q,
                         "date": str(meta.get("submitted_date", "")), "sentiment": sentiment})

    timeline.sort(key=lambda t: t["date"])
    return {
        "interviews_total": len(timeline),
        "by_department": {d: len(v) for d, v in by_dept.items()},
        "by_quarter": {q: len(v) for q, v in sorted(by_quarter.items())},
        "sentiment_by_department": {d: dict(c) for d, c in sentiment_by_dept.items()},
        "would_recommend_by_department": {d: dict(c) for d, c in recommend_by_dept.items()},
        "recurring_themes_by_department": {
            d: [{"theme": t, "interviews": n} for t, n in c.most_common() if n >= 1]
            for d, c in themes_by_dept.items()
        },
        "managers_of_leavers_by_department": {d: dict(c) for d, c in managers.items()},
        "timeline": timeline,
    }


def _split_alerts(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Separate the Markdown answer from the trailing ALERTS: json line."""
    m = re.search(r"ALERTS:\s*(\{.*\})\s*$", text, re.DOTALL)
    if not m:
        return text.strip(), []
    answer = text[: m.start()].strip()
    try:
        alerts = json.loads(m.group(1)).get("alerts") or []
    except json.JSONDecodeError:
        alerts = []
    return answer, [a for a in alerts if isinstance(a, dict)]


@register
class InterviewTrendAnalystAgent(BaseAgent):
    meta = AgentMeta(
        id="interview_trends",
        name="Exit Interview Trend Analyst",
        tagline="Longitudinal patterns + alerts",
        description=(
            "Analyzes every exit interview over time — by department and quarter — "
            "to surface emerging patterns like rising complaints under one manager, "
            "raises alerts, and answers follow-up questions in conversation."
        ),
        icon="↗︎",
        category="Analysis",
        fields=[
            Field("question", "Your question", type="textarea", required=False,
                  placeholder="Leave empty for the full trend report, or ask e.g. "
                              "\"What is changing in Sales?\""),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        question = (inputs.get("question") or "").strip()
        history = inputs.get("history") or []
        signals = _signals()
        if not signals["interviews_total"]:
            return {"error": "No exit interviews in the data store."}

        interviews_block = "\n\n---\n\n".join(
            f"[{iv.meta.get('interview_id', iv.slug)} | {iv.meta.get('department', '?')} | "
            f"{iv.meta.get('submitted_date', '?')} | sentiment: {iv.meta.get('sentiment', '?')}]\n"
            f"{iv.body.strip()}"
            for iv in store.all_interviews()
        )
        convo = "\n".join(
            f"{t.get('role', 'user').upper()}: {t.get('content', '')}" for t in history[-6:]
        )
        ask = question or (
            "Produce the longitudinal trend analysis: what patterns are emerging, "
            "where, and since when. Include a short 'Alerts' section."
        )
        user = (
            f"Aggregate signals:\n{json.dumps(signals, indent=2)}\n\n"
            f"Interviews:\n{interviews_block}\n\n"
            + (f"Conversation so far:\n{convo}\n\n" if convo else "")
            + f"Request: {ask}"
        )
        raw = llm.complete(SYSTEM, user, max_tokens=3000)
        answer, alerts = _split_alerts(raw)
        return {
            "answer": answer,
            "alerts": alerts,
            "sources": [
                {"source": t["interview_id"], "heading": f"{t['department']} · {t['date']}"}
                for t in signals["timeline"]
            ],
            "signals": signals,
        }
