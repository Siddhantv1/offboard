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

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from .. import ocr
from ..data_store import store

# Canonical required documents, mirroring data/process-docs/documents-required.md
# (proc-002). `issued` documents are produced by the company at sign-off, not
# collected from the employee, so they are tracked but never "missing".
REQUIRED_DOCUMENTS: list[dict[str, Any]] = [
    {"name": "Resignation letter", "stage": "Resignation / Notice"},
    {"name": "Notice acknowledgement", "stage": "Resignation / Notice"},
    {"name": "Personal contact details", "stage": "Resignation / Notice"},
    {"name": "KT document", "stage": "Knowledge Transfer"},
    {"name": "Successor acknowledgement", "stage": "Knowledge Transfer"},
    {"name": "Asset Return Checklist", "stage": "IT Clearance"},
    {"name": "Device handover acknowledgement", "stage": "IT Clearance"},
    {"name": "VPN/MFA deactivation confirmation", "stage": "IT Clearance"},
    {"name": "No-dues declaration", "stage": "Finance Clearance"},
    {"name": "Advance/loan settlement confirmation", "stage": "Finance Clearance", "conditional": True},
    {"name": "Bank account confirmation", "stage": "Finance Clearance"},
    {"name": "Exit interview form", "stage": "HR Clearance"},
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

VALIDATE_SYSTEM = """You validate scanned exit paperwork from OCR text. Decide \
whether the text is the expected document for the expected employee and whether \
it appears complete (signed, dated, key fields present). OCR may contain small \
errors; judge on substance.

Return JSON:
{
  "is_expected_document": bool,
  "detected_document_type": str,
  "employee_name_matches": bool,
  "signed": bool,
  "dated": bool,
  "missing_elements": [str],
  "verdict": "accepted|needs_review|rejected",
  "reason": str
}"""


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


def validate_upload(case_meta: dict[str, Any], document_name: str, image_bytes: bytes,
                    filename: str = "") -> dict[str, Any]:
    """OCR locally, then one short LLM judgement on the extracted text."""
    try:
        extracted = ocr.extract_text(image_bytes)
    except ocr.OCRUnavailable as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - bad image bytes etc.
        return {"error": f"Could not read the image: {exc}"}

    if not extracted["text"].strip():
        return {
            "document": document_name, "filename": filename, "ocr": extracted,
            "validation": {"verdict": "rejected", "reason": "No readable text found in the image."},
        }
    user = (
        f"Expected document: {document_name or 'unknown'}\n"
        f"Expected employee: {case_meta.get('employee_name', '?')} ({case_meta.get('employee_id', '?')})\n"
        f"Exit case: {case_meta.get('case_id', '?')}\n\n"
        f"OCR text ({extracted['engine']}"
        + (f", avg confidence {extracted['confidence']}" if extracted.get("confidence") is not None else "")
        + f"):\n{extracted['text']}"
    )
    judgement = llm.complete_json(VALIDATE_SYSTEM, user, max_tokens=800)
    return {"document": document_name, "filename": filename,
            "ocr": {k: extracted[k] for k in ("engine", "seconds", "confidence", "image_size", "text")},
            "validation": judgement}


@register
class DocumentCollectionAgent(BaseAgent):
    meta = AgentMeta(
        id="document_collection",
        name="Document Collection",
        tagline="Required vs submitted, reminders, OCR checks",
        description=(
            "Works out which forms an exit case needs at each stage, checks what has "
            "been submitted, drafts a reminder for what is missing, and validates an "
            "uploaded scan with local OCR before a quick model check."
        ),
        icon="📄",
        category="Communication",
        fields=[
            Field("case_id", "Exit case", type="select"),
            Field("document_name", "Document being uploaded", type="select", required=False,
                  options=[{"value": d["name"], "label": f"{d['name']} · {d['stage']}"}
                           for d in REQUIRED_DOCUMENTS if not d.get("issued")],
                  help="Only needed if you attach a scan below."),
            Field("upload", "Scan or photo of the form", type="file", required=False,
                  help="PNG/JPEG. Read locally with OCR; the image is not sent to the LLM."),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        case_id = (inputs.get("case_id") or "").strip()
        case = store.exit_case(case_id)
        if not case:
            return {"error": f"Unknown exit case '{case_id}'."}
        meta = case.meta
        status = document_status(meta)

        result: dict[str, Any] = {
            "case_id": meta.get("case_id", case_id),
            "employee": meta.get("employee_name"),
            "status": status,
        }

        upload = inputs.get("upload")
        if isinstance(upload, dict) and upload.get("bytes"):
            result["upload_validation"] = validate_upload(
                meta, (inputs.get("document_name") or "").strip(),
                upload["bytes"], filename=str(upload.get("filename", "")),
            )

        if status["missing"]:
            user = (
                f"Employee: {meta.get('employee_name')} <{store.employee(str(meta.get('employee_id', ''))).meta.get('email', '') if store.employee(str(meta.get('employee_id', ''))) else ''}>\n"
                f"Exit case: {meta.get('case_id')} · last working day {meta.get('last_working_day')}\n"
                f"Missing documents by stage:\n"
                + "\n".join(f"- {stage}: {', '.join(docs)}" for stage, docs in status["missing_by_stage"].items())
            )
            result["reminder"] = llm.complete_json(REMINDER_SYSTEM, user, temperature=0.4)
        else:
            result["reminder"] = None
        return result
