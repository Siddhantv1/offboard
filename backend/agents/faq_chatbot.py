"""Agent 3 — FAQ Chatbot for the Exit Process (RAG).

Answers employee questions about the exit process using retrieval over the
process docs + policies, and cites the sources it used. Supports multi-turn
history so it reads as a conversation.
"""
from __future__ import annotations

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ..rag.retriever import retrieve

SYSTEM = """You are a helpful HR assistant answering an exiting employee's \
questions about the offboarding process. Answer ONLY from the provided context \
passages. If the answer is not in the context, say you don't have that \
information and suggest who to contact. Be concise and reassuring. After the \
answer, list the sources you used by their titles."""


@register
class FAQChatbotAgent(BaseAgent):
    meta = AgentMeta(
        id="faq_chatbot",
        name="Exit Process FAQ Chatbot",
        tagline="Grounded answers about offboarding",
        description=(
            "A RAG chatbot that answers questions about timelines, steps, and "
            "documents by retrieving from the company's process docs and policies."
        ),
        icon="?",
        category="Communication",
        fields=[
            Field(
                "question",
                "Your question",
                type="textarea",
                placeholder="e.g. How long does the final settlement take? What documents do I need?",
            ),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        question = (inputs.get("question") or "").strip()
        if not question:
            return {"error": "Please enter a question."}

        history = inputs.get("history") or []  # optional [{role, content}]
        passages = retrieve(question, k=5)
        if not passages:
            context_block = "(no matching passages found)"
        else:
            context_block = "\n\n---\n\n".join(
                f"[Source: {p.source} > {p.heading}]\n{p.text}" for p in passages
            )

        convo_lines = [
            f"{turn.get('role', 'user').upper()}: {turn.get('content', '')}"
            for turn in history[-6:]
        ]
        convo_block = ""
        if convo_lines:
            convo_block = "Conversation so far:\n" + "\n".join(convo_lines) + "\n\n"

        user = (
            f"Context passages:\n{context_block}\n\n"
            f"{convo_block}"
            f"Question: {question}"
        )
        answer = llm.complete(SYSTEM, user, temperature=0.2)
        return {
            "answer": answer,
            "sources": [{"source": p.source, "heading": p.heading} for p in passages],
        }
