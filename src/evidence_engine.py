"""
Deterministic evidence matching engine.

Implements the algorithm from plan/02_evidence_matching.md:
  Step 0 – Early exit conditions
  Step 1 – Filter candidates (±2% amount tolerance, type match)
  Step 2 – Resolve verdict + relevant_transaction_id
    Scenario A – No match        → insufficient_data
    Scenario B – Single match    → consistent / inconsistent (Rules B1-B4)
    Scenario C – Multiple match  → disambiguate or insufficient_data
    Scenario D – Special cases   → duplicate, failed-payment, phishing

All operations are O(n) or O(n log n) in the number of transactions.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional, List

from .models import Transaction
from .preprocessor import ExtractedEntities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts: str) -> datetime:
    """Parse ISO-8601 timestamp, always returns tz-aware UTC datetime."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _amount_match(t_amount: float, c_amount: float) -> bool:
    """True if transaction amount is within ±2% of complaint amount."""
    if c_amount <= 0:
        return False
    return abs(t_amount - c_amount) / c_amount < 0.02


def _counterparty_match(t_cp: Optional[str], c_cp: Optional[str]) -> bool:
    """True if extracted counterparty matches transaction counterparty (or either is None)."""
    if c_cp is None or t_cp is None:
        return True  # can't contradict what we don't know
    return t_cp.lower().replace(" ", "-") == c_cp.lower().replace(" ", "-")


def _established_recipient_pattern(
    history: List[Transaction],
    counterparty: str,
    before_ts: datetime,
    window_days: int = 30,
    min_prior: int = 2,
) -> bool:
    """
    Rule B1: True if >= min_prior completed transfers to the same counterparty
    exist in the history within window_days before before_ts.
    """
    count = 0
    for t in history:
        if t.counterparty != counterparty:
            continue
        if t.type != "transfer":
            continue
        if t.status != "completed":
            continue
        ts = _parse_ts(t.timestamp)
        delta = (before_ts - ts).total_seconds()
        if 0 < delta <= window_days * 86400:
            count += 1
            if count >= min_prior:
                return True
    return False


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

class EvidenceResult:
    __slots__ = ("relevant_transaction_id", "evidence_verdict", "matched_transaction", "notes")

    def __init__(
        self,
        relevant_transaction_id: Optional[str],
        evidence_verdict: str,
        matched_transaction: Optional[Transaction] = None,
        notes: str = "",
    ) -> None:
        self.relevant_transaction_id = relevant_transaction_id
        self.evidence_verdict = evidence_verdict
        self.matched_transaction = matched_transaction
        self.notes = notes


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

def run_evidence_engine(
    entities: ExtractedEntities,
    history: List[Transaction],
    case_type: str,
) -> EvidenceResult:
    """
    Execute the full deterministic evidence matching algorithm.
    Returns an EvidenceResult with verdict and matched TXN (if any).
    """

    # ------------------------------------------------------------------
    # Step 0: Early exits
    # ------------------------------------------------------------------

    # 0a. Phishing – no matching needed by design
    if entities.is_phishing or case_type == "phishing_or_social_engineering":
        return EvidenceResult(None, "insufficient_data", notes="phishing_no_match_required")

    # 0b. Empty complaint / no entities and history empty
    if not entities.has_any_entity() and not history:
        return EvidenceResult(None, "insufficient_data", notes="no_entities_no_history")

    # 0c. Unauthorized transaction – always escalate, still try to find TXN
    if entities.is_unauthorized:
        # Try to find the disputed TXN; verdict stays insufficient_data until confirmed
        result = _match_candidates(entities, history, case_type)
        # Override verdict to insufficient_data to flag for fraud review
        return EvidenceResult(
            result.relevant_transaction_id,
            "insufficient_data",
            result.matched_transaction,
            notes="unauthorized_transaction_fraud_check",
        )

    # 0d. Vague complaint: no extractable entities, even with history present
    if not entities.has_any_entity():
        return EvidenceResult(None, "insufficient_data", notes="no_extractable_entities")

    # ------------------------------------------------------------------
    # Step 1 + 2: Normal matching
    # ------------------------------------------------------------------

    # Special case D: Duplicate payment detection
    if case_type == "duplicate_payment":
        return _detect_duplicate(entities, history)

    return _match_candidates(entities, history, case_type)


# ---------------------------------------------------------------------------
# Internal matching routines
# ---------------------------------------------------------------------------

def _match_candidates(
    entities: ExtractedEntities,
    history: List[Transaction],
    case_type: str,
) -> EvidenceResult:
    """Step 1 + 2: Filter, then apply Scenario A/B/C rules."""

    # --- Step 1: Filter candidates ---
    candidates: List[Transaction] = []
    for t in history:
        # Amount must match within ±2%
        if entities.amount is not None:
            if not _amount_match(t.amount, entities.amount):
                continue
        # Type must match (if detected)
        # EXCEPTION: refund_request case_type → the underlying transaction is a payment/transfer,
        # not a 'refund' type. Match payment/transfer/cash_out types for refund cases.
        if entities.txn_type is not None:
            expected_types = [entities.txn_type]
            if case_type == "refund_request":
                expected_types = ["payment", "transfer", "cash_out", "settlement"]
            elif case_type == "agent_cash_in_issue":
                expected_types = ["cash_in"]
            elif case_type == "merchant_settlement_delay":
                expected_types = ["settlement"]
            elif case_type in ("duplicate_payment", "payment_failed"):
                expected_types = ["payment", "transfer"]

            if t.type not in expected_types:
                continue
        candidates.append(t)

    # --- Scenario A: No match ---
    if not candidates:
        # If there are transactions but none match, it's insufficient
        return EvidenceResult(None, "insufficient_data", notes="no_candidates_found")

    # --- Scenario B: Single match ---
    if len(candidates) == 1:
        return _evaluate_single(entities, candidates[0], history, case_type)

    # --- Scenario C: Multiple matches ---
    return _resolve_multiple(entities, candidates, case_type)


def _evaluate_single(
    entities: ExtractedEntities,
    t: Transaction,
    history: List[Transaction],
    case_type: str,
) -> EvidenceResult:
    """Apply Rules B1-B4 to a single candidate transaction."""

    # Rule B1: Inconsistent recipient pattern (wrong_transfer claim)
    if case_type == "wrong_transfer" and t.type == "transfer" and t.counterparty:
        t_ts = _parse_ts(t.timestamp)
        if _established_recipient_pattern(history, t.counterparty, t_ts):
            return EvidenceResult(
                t.transaction_id, "inconsistent", t,
                notes="established_recipient_pattern"
            )

    # Rule B2: Pending transaction that matches non-receipt complaint
    if t.status == "pending":
        # Pending + complaint that money not received = consistent
        return EvidenceResult(
            t.transaction_id, "consistent", t,
            notes="pending_transaction_consistent"
        )

    # Rule B3: Contradictory claim vs status
    # Customer says "failed" but status is "completed" → inconsistent
    if t.status == "completed" and case_type in ("payment_failed",):
        # completed status contradicts claim of failure unless balance was deducted
        # Per SAMPLE-03: failed status + complaint = consistent
        # completed status + payment_failed claim = inconsistent
        return EvidenceResult(
            t.transaction_id, "inconsistent", t,
            notes="completed_but_claimed_failed"
        )

    # Rule B3b: Failed status + payment_failed claim = consistent (SAMPLE-03)
    if t.status == "failed" and case_type == "payment_failed":
        return EvidenceResult(t.transaction_id, "consistent", t, notes="failed_payment_consistent")

    # Rule B3c: Reversed + customer complains = inconsistent (already corrected)
    if t.status == "reversed":
        return EvidenceResult(
            t.transaction_id, "inconsistent", t,
            notes="already_reversed"
        )

    # Rule B4: Counterparty check if mentioned
    # EXCEPTION: For wrong_transfer, the complaint counterparty is the INTENDED number,
    # not necessarily the actual recipient. Skip counterparty check for wrong_transfer.
    if case_type != "wrong_transfer" and entities.counterparty and t.counterparty:
        if not _counterparty_match(t.counterparty, entities.counterparty):
            return EvidenceResult(
                t.transaction_id, "inconsistent", t,
                notes="counterparty_mismatch"
            )

    # Default: consistent
    return EvidenceResult(t.transaction_id, "consistent", t)


def _resolve_multiple(
    entities: ExtractedEntities,
    candidates: List[Transaction],
    case_type: str,
) -> EvidenceResult:
    """
    Scenario C: Multiple matches.
    Try to disambiguate via counterparty. If impossible → insufficient_data.
    """
    # Rule C1: Disambiguate via counterparty
    if entities.counterparty:
        matched = [
            t for t in candidates
            if _counterparty_match(t.counterparty, entities.counterparty)
        ]
        if len(matched) == 1:
            return _evaluate_single(entities, matched[0], [], case_type)

    # Rule C2: Still ambiguous — do not guess
    return EvidenceResult(None, "insufficient_data", notes="ambiguous_multiple_candidates")


def _detect_duplicate(
    entities: ExtractedEntities,
    history: List[Transaction],
) -> EvidenceResult:
    """
    Scenario D: Duplicate payment detection.
    Find two identical transactions (same amount, type, counterparty) within < 60 seconds.
    Returns the SECOND (duplicate) as the relevant_transaction_id.
    """
    # Filter by amount and type first
    same: List[Transaction] = []
    for t in history:
        if entities.amount is not None and not _amount_match(t.amount, entities.amount):
            continue
        same.append(t)

    if len(same) < 2:
        # Only one transaction – claim inconsistent (customer says duplicate, we only see one)
        if same:
            return EvidenceResult(same[0].transaction_id, "inconsistent", same[0], notes="only_one_transaction_no_duplicate")
        return EvidenceResult(None, "insufficient_data", notes="no_transactions_for_duplicate_check")

    # Sort by timestamp (not list order!) – O(n log n)
    same.sort(key=lambda t: _parse_ts(t.timestamp))

    # Check pairs for < 60 second windows
    for i in range(len(same) - 1):
        t1 = same[i]
        t2 = same[i + 1]
        delta = abs((_parse_ts(t2.timestamp) - _parse_ts(t1.timestamp)).total_seconds())
        # Same counterparty (or not mentioned)
        same_cp = (
            (t1.counterparty or "").lower() == (t2.counterparty or "").lower()
        )
        if delta < 60 and same_cp and t1.type == t2.type:
            # t2 is the duplicate (second transaction)
            return EvidenceResult(t2.transaction_id, "consistent", t2, notes=f"duplicate_detected_{delta:.0f}s_apart")

    # Candidates exist but no < 60s pair
    # Still return the most recent match as inconsistent
    return EvidenceResult(same[-1].transaction_id, "inconsistent", same[-1], notes="no_close_duplicate_found")
