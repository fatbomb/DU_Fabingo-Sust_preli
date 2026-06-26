"""
QueueStorm Investigator — FastAPI Application Entry Point.

Endpoints:
  GET  /health           → {"status": "ok"}
  POST /analyze-ticket   → TicketOutput JSON

Processing pipeline:
  1. Parse & validate input (Pydantic)
  2. Preprocess complaint → extract entities
  3. Classify case_type, severity, human_review
  4. Run deterministic evidence engine
  5. Compute department routing
  6. Call LLM (agent_summary + customer_reply) with 15s timeout
  7. Apply safety guardrails to all text fields
  8. Persist result to Supabase (best-effort, non-blocking)
  9. Return TicketOutput
"""
from __future__ import annotations

# Load .env file — must happen before any os.environ reads in other modules
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass  # python-dotenv not installed in minimal environments

import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any

# Ensure the parent directory is in the path to allow direct execution (e.g. `python src/main.py`)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.models import TicketInput, TicketOutput
from src.preprocessor import preprocess
from src.classifier import (
    classify_case_type,
    classify_severity,
    classify_human_review,
    compute_confidence,
    compute_reason_codes,
)
from src.evidence_engine import run_evidence_engine
from src.route import route_department
from src.llm_client import call_llm
from src.rule_fallback import generate_fallback_response
from src.safety import sanitize_all
from src.database import persist_ticket_result

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("queuestorm")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="QueueStorm Investigator",
    description="AI-powered ticket analysis for a major digital finance platform.",
    version="1.0.0",
)


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("QueueStorm Investigator starting up on port %s", os.environ.get("PORT", "8000"))
    logger.info(
        "LLM: model=%s timeout=%ss",
        os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        os.environ.get("LLM_TIMEOUT_SECONDS", "15"),
    )
    logger.info("DB persistence: %s", os.environ.get("ENABLE_DB_PERSISTENCE", "false"))


# ---------------------------------------------------------------------------
# Error handlers (never leak stack traces or secrets)
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "Invalid input", "detail": exc.errors()},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled error: %s", traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error. Please try again later."},
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/")
async def root() -> dict:
    """Root endpoint, friendly welcome message."""
    return {
        "service": "QueueStorm Investigator API",
        "status": "running",
        "message": "Send POST requests to /analyze-ticket to use this service."
    }

@app.get("/health")
async def health() -> dict:
    """Startup health check — must respond within 60 seconds of boot."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@app.post("/analyze-ticket", response_model=TicketOutput)
async def analyze_ticket(ticket: TicketInput) -> TicketOutput:
    """
    Analyze a customer support ticket and return structured investigation results.
    """
    try:
        return _process_ticket(ticket)
    except Exception as exc:
        # Last-resort: log the error but never return a 500 with sensitive info
        logger.error("Error processing ticket %s: %s", ticket.ticket_id, str(exc))
        raise


def _process_ticket(ticket: TicketInput) -> TicketOutput:
    """
    Core synchronous processing pipeline.
    This is separated from the async handler so it can be tested directly.
    """
    complaint = ticket.complaint.strip()
    language = ticket.language or "mixed"
    user_type = ticket.user_type or "customer"
    history = ticket.transaction_history or []

    # -----------------------------------------------------------------------
    # Step 1: Preprocess — extract entities
    # -----------------------------------------------------------------------
    entities = preprocess(complaint, language)

    # Use detected language to override input if input says "mixed"
    effective_language = entities.detected_language if language == "mixed" else language

    # -----------------------------------------------------------------------
    # Step 2: Classify case_type
    # -----------------------------------------------------------------------
    case_type = classify_case_type(complaint, entities, user_type)

    # -----------------------------------------------------------------------
    # Step 3: Evidence matching
    # -----------------------------------------------------------------------
    evidence = run_evidence_engine(entities, history, case_type)

    # -----------------------------------------------------------------------
    # Step 4: Department routing
    # -----------------------------------------------------------------------
    department = route_department(
        case_type=case_type,
        evidence_verdict=evidence.evidence_verdict,
        user_type=user_type,
        complaint=complaint,
        is_unauthorized=entities.is_unauthorized,
    )

    # -----------------------------------------------------------------------
    # Step 5: Severity, human_review, confidence, reason_codes
    # -----------------------------------------------------------------------
    severity = classify_severity(case_type, evidence.evidence_verdict, entities, entities.is_unauthorized)
    human_review = classify_human_review(case_type, evidence.evidence_verdict, entities, severity)
    confidence = compute_confidence(case_type, evidence.evidence_verdict, entities, evidence.notes)
    reason_codes = compute_reason_codes(case_type, evidence.evidence_verdict, evidence.notes, entities)

    # -----------------------------------------------------------------------
    # Step 6: LLM call (agent_summary + customer_reply)
    # -----------------------------------------------------------------------
    llm_result = call_llm(
        complaint=complaint,
        language=effective_language,
        case_type=case_type,
        evidence_verdict=evidence.evidence_verdict,
        relevant_txn_id=evidence.relevant_transaction_id,
        user_type=user_type,
    )

    # -----------------------------------------------------------------------
    # Step 7: Build response (LLM or fallback)
    # -----------------------------------------------------------------------
    if not llm_result.success:
        logger.info("LLM unavailable for ticket %s — using rule-based fallback", ticket.ticket_id)
        return generate_fallback_response(
            ticket=ticket,
            entities=entities,
            evidence=evidence,
            case_type=case_type,
            severity=severity,
            human_review=human_review,
            confidence=confidence,
            reason_codes=reason_codes,
        )

    # Build response from LLM fields + deterministic fields
    response_dict = {
        "ticket_id": ticket.ticket_id,
        "relevant_transaction_id": evidence.relevant_transaction_id,
        "evidence_verdict": evidence.evidence_verdict,
        "case_type": case_type,
        "severity": severity,
        "department": department,
        "agent_summary": llm_result.agent_summary,
        "recommended_next_action": _build_recommended_action(case_type, evidence, department, entities),
        "customer_reply": llm_result.customer_reply,
        "human_review_required": human_review,
        "confidence": round(confidence, 2),
        "reason_codes": reason_codes,
    }

    # -----------------------------------------------------------------------
    # Step 8: Safety guardrails (overwrite any unsafe LLM output)
    # -----------------------------------------------------------------------
    response_dict = sanitize_all(response_dict, effective_language)

    result = TicketOutput(**response_dict)

    # -----------------------------------------------------------------------
    # Step 9: Persist to Supabase (best-effort, never blocks response)
    # -----------------------------------------------------------------------
    try:
        persist_ticket_result(response_dict)
    except Exception as db_exc:
        logger.warning("DB persistence skipped: %s", str(db_exc))

    return result


# ---------------------------------------------------------------------------
# Recommended action builder (shared between main and fallback)
# ---------------------------------------------------------------------------

def _build_recommended_action(
    case_type: str,
    evidence,
    department: str,
    entities,
) -> str:
    """Build deterministic recommended_next_action (internal English field)."""
    txn_ref = evidence.relevant_transaction_id or "the relevant transaction"

    actions = {
        "wrong_transfer": (
            f"Verify {txn_ref} details with the customer and initiate the wrong-transfer "
            f"dispute workflow per policy."
        ),
        "payment_failed": (
            f"Investigate {txn_ref} ledger status. If balance was deducted on a failed payment, "
            f"initiate the automatic reversal flow within standard SLA."
        ),
        "duplicate_payment": (
            f"Verify the duplicate with payments_ops. If the biller confirms only one payment "
            f"was received, initiate reversal of {txn_ref}."
        ),
        "refund_request": (
            "Inform the customer that refund eligibility depends on the merchant's own policy. "
            "Provide guidance on contacting the merchant directly for a refund."
        ),
        "merchant_settlement_delay": (
            f"Route to merchant_operations to verify settlement batch status for {txn_ref}. "
            "If the batch is delayed, communicate a revised ETA to the merchant."
        ),
        "agent_cash_in_issue": (
            f"Investigate {txn_ref} pending status with agent operations. "
            "Confirm settlement state and resolve within the standard cash-in SLA."
        ),
        "phishing_or_social_engineering": (
            "Escalate to fraud_risk team immediately. Confirm to customer that the platform "
            "never asks for OTP. Log the reported number/contact for fraud pattern analysis."
        ),
        "other": (
            "Reply to customer asking for specific details: which transaction, what amount, "
            "what went wrong, and approximate time. Do not initiate any dispute without confirmation."
        ),
    }

    if entities.is_unauthorized:
        return (
            f"Escalate to fraud_risk immediately. Investigate {txn_ref} for unauthorized access. "
            "Freeze account if compromise is confirmed. Contact customer through verified channels only."
        )

    return actions.get(case_type, actions["other"])


if __name__ == "__main__":
    import uvicorn
    # Defaults to port 8000, can be overridden by PORT env var
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, reload=True)
