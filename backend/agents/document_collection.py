"""Agent 12 — Document Collection Agent.

For an exit case: works out which documents are required at each stage (per
the "Documents & Forms Required" process doc), checks which the employee has
submitted (the case's `documents_submitted` list), drafts reminders for the
ones that are missing and due, and validates an uploaded form.

Upload validation is *local OCR first*: `backend/ocr.py` extracts the text on
this machine with a small ONNX model, and only that text goes to the LLM for a
short judgement (right document? right employee? signed and dated?). The image
never leaves the machine and the LLM call is a cheap text call.
"""
from __future__ import annotations

import sys
from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import require_case
from .. import ocr
from ..data_store import store
from ..rag import retriever as rag
from ..db import session_scope
from ..db.models import CaseDocument
from ..llm import LLMError

# Canonical required documents, mirroring data/process-docs/documents-required.md
# (proc-002). `issued` documents are produced by the company at sign-off, not
# collected from the employee, so they are tracked but never "missing".
#
# Three more were removed for asking twice.
#
# A *resignation letter* and *personal contact details* are requested from
# somebody who resigned through this product and whose record already holds
# their email, their manager, their department and their last working day.
# Asking them to upload a PDF of what we typed into the form is paperwork for
# its own sake.
#
# The *exit interview form* is collected as an interview, on its own page, with
# stable question ids so `interview_trends` can compare quarters. Asking for a
# scan of it as well produced two records of the same conversation, one of them
# unreadable by anything.
#
# IT Clearance deliberately has NO documents. It used to ask for three -- an
# asset return checklist, a device handover acknowledgement and a VPN/MFA
# deactivation confirmation -- none of which is a document anybody actually
# holds: the laptop goes back to a desk and IT reads the truth out of their own
# asset system. Chasing paperwork for it was theatre that a leaver could
# satisfy with a blank PDF. It is a two-sided mark now instead: the employee
# confirms they handed everything back (`CaseStage.employee_confirmed_at`) and
# IT verifies independently by completing the stage.
REQUIRED_DOCUMENTS: list[dict[str, Any]] = [
    {"name": "Notice acknowledgement", "stage": "Resignation / Notice"},
    {"name": "KT document", "stage": "Knowledge Transfer"},
    {"name": "Successor acknowledgement", "stage": "Knowledge Transfer"},
    {"name": "No-dues declaration", "stage": "Finance Clearance"},
    {"name": "Advance/loan settlement confirmation", "stage": "Finance Clearance", "conditional": True},
    {"name": "Bank account confirmation", "stage": "Finance Clearance"},
    {"name": "Confidentiality Reaffirmation", "stage": "HR Clearance"},
    {"name": "ID badge return acknowledgement", "stage": "HR Clearance"},
    {"name": "PF transfer/withdrawal form", "stage": "HR Clearance"},
    {"name": "Relieving letter", "stage": "Final Sign-off", "issued": True},
    {"name": "Experience letter", "stage": "Final Sign-off", "issued": True},
    {"name": "Full-and-final settlement statement", "stage": "Final Sign-off", "issued": True},
]

# Case stage names -> the document stages whose paperwork is due once that
# case stage is open. Resignation paperwork is due from day one.
_STAGE_DUE_ORDER = ["Resignation / Notice", "IT Clearance", "Finance Clearance",
                    "HR Clearance", "Knowledge Transfer", "Manager Sign-off", "Final Sign-off"]

REMINDER_SYSTEM = """You are an HR operations coordinator. Write ONE friendly but \
clear reminder email to an exiting employee listing the documents they still \
need to submit, grouped by clearance stage, with why each matters and the \
deadline (their last working day). Keep it under 200 words.

Return JSON: {"to": str, "subject": str, "body": str, "urgency": "low|medium|high"}"""

VALIDATE_SYSTEM = """You validate exit paperwork from its extracted text.

The text comes either from OCR of a photo or scan, or read exactly out of a \
PDF or HTML file. The prompt says which. Apply the OCR tolerance below only \
when it says OCR.

You are given the company's own policy text for this document type. Judge the \
scan against THOSE stated requirements, not against your general idea of what \
the document should contain -- if policy names required sections or fields, \
check for those specifically and report which are present.

OCR contains small errors. Judge on substance: a garbled character in a name or \
date is not a missing field. Only call something missing if it genuinely is not \
there.

Verdicts:
- "accepted": it is the right document, for the right person, and meets the \
  stated requirements.
- "needs_review": probably fine but something is unclear, unreadable, or policy \
  is silent on it. A human will look.
- "rejected": it is the wrong document, the wrong person, or plainly incomplete.

Return JSON:
{
  "is_expected_document": bool,
  "detected_document_type": str,
  "employee_name_matches": bool,
  "signed": bool,
  "dated": bool,
  "requirements_met": [str],
  "missing_elements": [str],
  "verdict": "accepted|needs_review|rejected",
  "reason": str
}"""


def _policy_requirements(document_name: str, stage: str = "") -> tuple[str, list[str]]:
    """What the company's own documents say this form must contain.

    Returns (text_for_the_prompt, source_names).

    Without this the validator was judging a scan against the model's general
    idea of what, say, a KT document looks like. The company has an actual
    answer -- `data/process-docs/documents-required.md` names four mandatory KT
    sections, and the NDA policy states what a Confidentiality Reaffirmation
    has to reaffirm -- and it is already indexed for the FAQ chatbot. Reusing
    that index means "is this valid?" is answered against the real rule rather
    than a plausible-sounding one.

    Failure here is never fatal: a degraded index should cost us grounding, not
    the upload.
    """
    query = f"{document_name} {stage} required contents sections signature".strip()
    try:
        passages = rag.retrieve(query, k=3)
    except Exception as exc:  # noqa: BLE001 - grounding is a bonus, not a gate
        print(f"[document_collection] policy lookup failed for {document_name!r}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return "", []

    if not passages:
        return "", []

    blocks = []
    sources = []
    for passage in passages:
        blocks.append(f"[{passage.source} - {passage.heading}]\n{passage.text.strip()}")
        if passage.source not in sources:
            sources.append(passage.source)
    return "\n\n".join(blocks), sources


def _norm(s: str) -> str:
    return " ".join(str(s).lower().replace("&", "and").split())


def document_status(case_meta: dict[str, Any]) -> dict[str, Any]:
    """Pure-code comparison of required vs submitted documents for a case."""
    submitted = {_norm(d) for d in case_meta.get("documents_submitted") or []}
    stages = {str(s.get("name")): str(s.get("status", "pending")).lower()
              for s in case_meta.get("stages") or [] if isinstance(s, dict)}

    def due(doc_stage: str) -> bool:
        if doc_stage == "Resignation / Notice":
            return True
        if doc_stage == "Final Sign-off":
            return False
        return stages.get(doc_stage, "pending") in ("in_progress", "completed")

    rows: list[dict[str, Any]] = []
    for d in REQUIRED_DOCUMENTS:
        is_sub = _norm(d["name"]) in submitted
        if d.get("issued"):
            status = "issued" if is_sub else "to_be_issued"
        elif is_sub:
            status = "submitted"
        elif due(d["stage"]):
            status = "missing"
        else:
            status = "not_yet_due"
        rows.append({
            "document": d["name"], "stage": d["stage"], "status": status,
            "case_stage_status": stages.get(d["stage"], "n/a" if d["stage"] in ("Resignation / Notice", "Final Sign-off") else "pending"),
            "conditional": bool(d.get("conditional")),
        })
    missing = [r for r in rows if r["status"] == "missing"]
    collectable = [r for r in rows if not r["document"] in {d["name"] for d in REQUIRED_DOCUMENTS if d.get("issued")}]
    submitted_n = sum(1 for r in collectable if r["status"] == "submitted")
    return {
        "documents": rows,
        "missing": missing,
        "missing_by_stage": _group(missing),
        "submitted_count": submitted_n,
        "required_now_count": submitted_n + len(missing),
        "collectable_total": len(collectable),
        "completion_pct": round(100 * submitted_n / max(1, submitted_n + len(missing))),
        "all_due_documents_in": not missing,
    }


def _group(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["stage"], []).append(r["document"])
    return out


def record_upload(*, case_id: str, document_name: str, filename: str,
                  content_type: str, image_bytes: bytes,
                  extracted: dict[str, Any], judgement: dict[str, Any],
                  uploaded_by: str | None = None) -> int | None:
    """Persist a validated upload against the case; return its row id.

    Stored whatever the verdict. A rejected scan is evidence that someone
    submitted something, which matters in an audit -- it just does not count
    towards `documents_submitted` (see `ExitCase.submitted_documents`).

    Returns None rather than raising if the write fails: the operator has
    already been shown a verdict, and turning a storage error into a 500 would
    lose that verdict too. The failure is reported in the response instead.
    """
    verdict = str(judgement.get("verdict") or "needs_review").strip().lower()
    confidence = extracted.get("confidence")
    seconds = extracted.get("seconds")
    try:
        with session_scope() as session:
            row = CaseDocument(
                case_id=case_id,
                document_name=document_name,
                filename=filename or "",
                content_type=content_type or "",
                ocr_engine=extracted.get("engine"),
                ocr_text=extracted.get("text") or "",
                ocr_confidence=float(confidence) if confidence is not None else None,
                ocr_seconds=float(seconds) if seconds is not None else None,
                verdict=verdict,
                reason=str(judgement.get("reason") or ""),
                validation=judgement,
                image=image_bytes or None,
                image_bytes=len(image_bytes) if image_bytes else None,
                uploaded_by=uploaded_by,
            )
            session.add(row)
            session.flush()
            return row.id
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller, not raised
        print(f"[document_collection] could not persist upload for "
              f"{case_id}/{document_name!r}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


#: Document stages whose acceptance is a named person's judgement, not a
#: model's.
#:
#: The handover is the manager's call. The codebase already says so twice --
#: `policy.ROLE_AGENTS_EXCEPT` takes the KT reviewer off HR because "reviewing
#: a handover is the manager's call: they know what the work was and who is
#: picking it up" -- and then the upload validator quietly overrode both by
#: writing `accepted` the moment the OCR text looked plausible. Rahul Verma's
#: handover was accepted by a model that has never met his successor, and the
#: manager who has arrived to find the decision already made.
#:
#: The model's read is kept in full; only the verdict waits. This is the same
#: rule the employee's own IT confirmation follows: the reading is evidence,
#: and the sign-off belongs to whoever is accountable for it.
HUMAN_ACCEPTED_STAGES = frozenset({"Knowledge Transfer"})


def _stage_of(document_name: str) -> str:
    return next((d["stage"] for d in REQUIRED_DOCUMENTS
                 if _norm(d["name"]) == _norm(document_name)), "")


def who_accepts(document_name: str) -> str | None:
    """The role whose decision this document's verdict is, or None when the
    model's read stands on its own. Read by the review route to decide who may
    overturn a verdict, so "who accepts this" is answered in one place."""
    return "manager" if _stage_of(document_name) in HUMAN_ACCEPTED_STAGES else None


#: Extractors whose output is the document's own characters rather than a
#: guess at them. Worth telling the model, because `VALIDATE_SYSTEM` instructs
#: it to forgive garbled characters -- forgiveness that is correct for a photo
#: and wrong for a PDF's text layer, where a missing field really is missing.
_EXACT_ENGINES = {"pdf-text", "html-text"}


def _source_line(extracted: dict[str, Any]) -> str:
    engine = str(extracted.get("engine") or "")
    if engine in _EXACT_ENGINES:
        return (f"Text read exactly from the file ({engine}) — this is not OCR, "
                "so anything absent here is genuinely absent from the document")
    confidence = extracted.get("confidence")
    return (f"OCR text ({engine}"
            + (f", avg confidence {confidence}" if confidence is not None else "")
            + ")")


def validate_upload(case_meta: dict[str, Any], document_name: str, image_bytes: bytes,
                    filename: str = "", content_type: str = "",
                    uploaded_by: str | None = None) -> dict[str, Any]:
    """OCR locally, judge the text, then persist the result against the case.

    The persistence is the point: this used to return a verdict and write
    nothing, so an accepted document never appeared in the case and the next
    run still reported it missing.
    """
    try:
        extracted = ocr.extract_document(image_bytes, content_type, filename)
    except (ocr.OCRUnavailable, ocr.UnreadableUpload) as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - corrupt bytes etc.
        return {"error": f"Could not read that file: {exc}"}

    case_id = str(case_meta.get("case_id") or "").strip()

    policy_sources: list[str] = []
    if not extracted["text"].strip():
        # Said in terms of what they sent. "No readable text found in the
        # image" on a PDF reads as though the upload went to the wrong place.
        judgement = {"verdict": "rejected",
                     "reason": {
                         "pdf": "No readable text in that PDF. If it is a scan, "
                                "send the scan itself as a PNG or JPG.",
                         "html": "That HTML file has no visible text in it.",
                     }.get(extracted.get("kind"),
                           "No readable text found in the image.")}
    else:
        stage = _stage_of(document_name)
        policy_text, policy_sources = _policy_requirements(document_name, stage)

        user = (
            f"Expected document: {document_name or 'unknown'}\n"
            f"Expected employee: {case_meta.get('employee_name', '?')} ({case_meta.get('employee_id', '?')})\n"
            f"Exit case: {case_meta.get('case_id', '?')}\n\n"
            + (f"What company policy requires of this document:\n{policy_text}\n\n"
               if policy_text else
               "No policy text was found for this document type; judge on general "
               "completeness and say so in the reason.\n\n")
            + _source_line(extracted)
            + f":\n{extracted['text']}"
        )
        try:
            judgement = llm.complete_json(VALIDATE_SYSTEM, user, max_tokens=800)
        except LLMError as exc:
            # The OCR read above already succeeded and is worth keeping even
            # if the judgement call fails -- without this, a gateway hiccup
            # on this cheap text call would discard a correctly-read scan and
            # force the employee to re-upload for no fault of their own.
            judgement = {"verdict": "needs_review",
                         "reason": f"Automatic validation was unavailable ({exc}); "
                                   "an HR reviewer should check this document "
                                   "manually."}

        # The model read it; it does not get to accept it. Held at
        # `needs_review` so the document still shows as outstanding -- which is
        # the truth, because nobody with the authority has looked -- and the
        # reason it gave is kept as the advice it is.
        decider = who_accepts(document_name)
        if decider and str(judgement.get("verdict") or "") == "accepted":
            judgement = {
                **judgement,
                "verdict": "needs_review",
                "reason": (str(judgement.get("reason") or "").strip()
                           + f" Read and found in order, but accepting a "
                             f"{stage.lower()} document is the {decider}'s "
                             "decision, so it is waiting on them.").strip(),
            }

    out: dict[str, Any] = {
        "document": document_name,
        "filename": filename,
        # `.get`, because only the image path has an `image_size` -- a PDF
        # reports `pages` instead and an HTML file neither.
        "ocr": {k: extracted.get(k) for k in
                ("engine", "kind", "seconds", "confidence", "image_size",
                 "pages", "text")},
        "validation": judgement,
        # Which policy documents the judgement was grounded in. Shown to the
        # person who uploaded, so a rejection cites a rule rather than an
        # opinion.
        "checked_against": policy_sources,
    }

    # An unreadable scan is recorded too: "somebody submitted a blank page" is
    # a fact the case should carry, not something to drop on the floor.
    if not case_id:
        out["persisted"] = {"error": "No case id on the record; upload not saved."}
        return out

    row_id = record_upload(
        case_id=case_id, document_name=document_name, filename=filename,
        content_type=content_type, image_bytes=image_bytes,
        extracted=extracted, judgement=judgement, uploaded_by=uploaded_by,
    )
    out["persisted"] = (
        {"id": row_id, "case_id": case_id} if row_id is not None
        else {"error": "Validated, but the upload could not be saved. "
                       "The verdict above is not recorded against the case."}
    )
    return out


@register
class DocumentCollectionAgent(BaseAgent):
    meta = AgentMeta(
        id="document_collection",
        name="Document Chaser",
        tagline="Who still owes what, and a reminder to send",
        description=(
            "Lists the forms this exit case still needs, works out which are "
            "actually due yet given where the clearance has got to, and drafts "
            "one reminder email covering all of them."
        ),
        icon="▤",
        category="Communication",
        fields=[
            Field("case_id", "Exit case", type="select"),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        case = require_case(inputs.get("case_id"))
        if isinstance(case, dict):
            return case
        meta = case.meta
        case_id = str(meta.get("case_id") or "")

        result: dict[str, Any] = {
            "case_id": meta.get("case_id", case_id),
            "employee": meta.get("employee_name"),
        }

        status = document_status(meta)
        result["status"] = status

        if status["missing"]:
            employee = store.employee(str(meta.get("employee_id", "")))
            email = employee.meta.get("email", "") if employee else ""
            user = (
                f"Employee: {meta.get('employee_name')} <{email}>\n"
                f"Exit case: {meta.get('case_id')} · last working day {meta.get('last_working_day')}\n"
                f"Missing documents by stage:\n"
                + "\n".join(f"- {stage}: {', '.join(docs)}"
                            for stage, docs in status["missing_by_stage"].items())
            )
            result["reminder"] = llm.complete_json(REMINDER_SYSTEM, user, temperature=0.4)
        else:
            result["reminder"] = None
        return result
