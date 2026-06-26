"""
Supabase database client for QueueStorm Investigator.

Persists ticket analysis results to Supabase for:
  - Audit trail and compliance logging
  - Analytics on case type distribution
  - Human review queue management

All credentials are loaded from environment variables ONLY.
No keys are hardcoded anywhere in this file.

Table schema (run the SQL in supabase_schema.sql to create):
  ticket_analyses (
    id               uuid primary key default gen_random_uuid(),
    ticket_id        text not null,
    case_type        text,
    evidence_verdict text,
    severity         text,
    department       text,
    relevant_txn_id  text,
    human_review     boolean,
    confidence       float,
    reason_codes     text[],
    created_at       timestamptz default now()
  )
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("queuestorm.db")

# ---------------------------------------------------------------------------
# Lazy Supabase client (only initialized if credentials are present)
# ---------------------------------------------------------------------------
_supabase_client = None
_db_enabled = False


def _get_client():
    """Get or create the Supabase client. Returns None if not configured."""
    global _supabase_client, _db_enabled

    if _supabase_client is not None:
        return _supabase_client

    url = os.environ.get("SUPABASE_URL", "").strip()
    # Prefer service role key for write operations; fall back to anon
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.environ.get("SUPABASE_ANON_KEY", "").strip()
    )
    enabled = os.environ.get("ENABLE_DB_PERSISTENCE", "false").lower() == "true"

    if not enabled or not url or not key:
        _db_enabled = False
        return None

    try:
        from supabase import create_client  # type: ignore
        _supabase_client = create_client(url, key)
        _db_enabled = True
        logger.info("Supabase persistence enabled: %s", url)
        return _supabase_client
    except Exception as e:
        logger.warning("Supabase client init failed: %s. Persistence disabled.", str(e))
        _db_enabled = False
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def persist_ticket_result(output_dict: dict) -> None:
    """
    Persist a ticket analysis result to Supabase.
    Silently skips if DB is not configured or disabled.
    This is a best-effort operation — never raises on failure.
    """
    client = _get_client()
    if client is None:
        return

    try:
        record = {
            "ticket_id": output_dict.get("ticket_id"),
            "case_type": output_dict.get("case_type"),
            "evidence_verdict": output_dict.get("evidence_verdict"),
            "severity": output_dict.get("severity"),
            "department": output_dict.get("department"),
            "relevant_txn_id": output_dict.get("relevant_transaction_id"),
            "human_review": output_dict.get("human_review_required", False),
            "confidence": output_dict.get("confidence"),
            "reason_codes": output_dict.get("reason_codes", []),
        }
        client.table("ticket_analyses").insert(record).execute()
        logger.debug("Persisted ticket %s to Supabase", output_dict.get("ticket_id"))
    except Exception as e:
        # DB errors must NEVER affect the API response
        logger.warning("Supabase persistence failed for ticket %s: %s", output_dict.get("ticket_id"), str(e))


def is_persistence_enabled() -> bool:
    """Check if Supabase persistence is active."""
    return _db_enabled
