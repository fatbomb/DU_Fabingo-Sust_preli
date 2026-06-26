"""
Rule-based ticket classifier.

Implements plan/03_classification_routing.md:
  - case_type classification from keywords
  - severity matrix
  - human_review_required flags
  - confidence score generation
  - reason code generation

This is entirely deterministic – no LLM required.
The LLM contributes to agent_summary and customer_reply only.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .preprocessor import ExtractedEntities


# ---------------------------------------------------------------------------
# Case-type keyword sets  (ordered by priority: more specific first)
# ---------------------------------------------------------------------------

_PHISHING_KW = [
    "asked for my otp", "asked for my pin", "asked for my password",
    "they asked", "called saying", "called me saying", "claimed to be from",
    "impersonat", "account block", "account will be blocked",
    "account suspended",
    "সে পিন চেয়েছে", "ওটিপি চেয়েছে", "ওটিপি চাইলো",
    "একাউন্ট বন্ধ করবে", "ফোন করেছে", "ফোন দিয়েছে",
    "bKash থেকে ফোন", "bkash er lok", "ব্লক হবে",
]

_WRONG_TRANSFER_KW = [
    "wrong number", "wrong transfer", "wrong person", "wrong recipient",
    "typing mistake", "accidentally sent", "mistakenly sent",
    "ভুল নম্বর", "ভুল নাম্বার", "ভুল মানুষ", "ভুল নাম্বারে",
    "অন্য নাম্বারে পাঠিয়েছি", "ভুলে পাঠিয়েছি",
    "ভুল টাকা পাঠিয়েছি",
    "wrong amount",
    # Non-receipt of a sent transfer: "I sent X to my brother but he didn't get it"
    "didn't get it", "did not get it", "didn't receive", "did not receive",
    "not received", "hasn't received", "says he didn't", "says she didn't",
    "পায়নি", "পাচ্ছে না",
]

_PAYMENT_FAILED_KW = [
    "payment failed", "failed payment", "transaction failed",
    "recharge failed", "balance deducted", "money deducted",
    "failed but", "showed failed", "পেমেন্ট ব্যর্থ", "টাকা কেটেছে",
    "রিচার্জ হয়নি", "পেমেন্ট হয়নি", "ব্যালেন্স কেটেছে",
    "failed কিন্তু টাকা",
]

_DUPLICATE_PAYMENT_KW = [
    "twice", "double", "duplicate", "charged twice", "deducted twice",
    "double payment", "two times", "2 times",
    "দুইবার", "ডাবল", "দুবার কেটেছে", "দুই বার কেটেছে",
    "টাকা ২ বার", "আবার কেটেছে",
]

_REFUND_REQUEST_KW = [
    "refund", "return my money", "changed my mind", "don't want",
    "cancel order", "cancel payment",
    "ফেরত চাই", "রিফান্ড চাই", "ফেরত দিন",
    "প্রোডাক্ট নেব না", "বাতিল করুন",
]

_MERCHANT_SETTLEMENT_KW = [
    "settlement", "settlement delay", "sales not settled",
    "merchant settlement", "sales money", "settlement not received",
    "সেটেলমেন্ট", "সেটেলমেন্ট হয়নি", "বিক্রয়ের টাকা",
    "সেটেলমেন্ট বাকি", "পেমেন্ট পাইনি merchant",
]

_AGENT_CASH_IN_KW = [
    "cash in", "cash-in", "agent cash", "agent deposit",
    "ক্যাশ ইন", "এজেন্ট ক্যাশ", "এজেন্ট থেকে টাকা",
    "এজেন্টের কাছে জমা", "cash in koreci", "ক্যাশইন",
    "cash in করেছি",
]


def _any_kw(text: str, keywords: List[str]) -> bool:
    """Case-insensitive substring match for any keyword."""
    lower = text.lower()
    return any(kw.lower() in lower for kw in keywords)


def classify_case_type(
    complaint: str,
    entities: ExtractedEntities,
    user_type: str = "customer",
) -> str:
    """
    Classify complaint into one of the 8 allowed case_type enums.
    Priority: phishing > unauthorized > duplicate > agent_cash_in >
              wrong_transfer > payment_failed > merchant_settlement >
              refund_request > other
    """

    # 1. Phishing (highest priority – safety concern)
    if entities.is_phishing or _any_kw(complaint, _PHISHING_KW):
        return "phishing_or_social_engineering"

    # 2. Unauthorized transaction (account compromise → other, routed to fraud_risk)
    if entities.is_unauthorized:
        return "other"

    # 3. Duplicate payment (check before payment_failed to catch "twice" correctly)
    if _any_kw(complaint, _DUPLICATE_PAYMENT_KW):
        return "duplicate_payment"

    # 4. Agent cash-in issue
    if _any_kw(complaint, _AGENT_CASH_IN_KW) and user_type != "merchant":
        return "agent_cash_in_issue"

    # 5. Merchant settlement delay (primarily merchant user_type)
    if _any_kw(complaint, _MERCHANT_SETTLEMENT_KW) or user_type == "merchant":
        # But only if it actually matches settlement keywords
        if _any_kw(complaint, _MERCHANT_SETTLEMENT_KW):
            return "merchant_settlement_delay"
        if user_type == "merchant" and _any_kw(complaint, ["settlement", "sales", "not received", "delay", "সেটেলমেন্ট"]):
            return "merchant_settlement_delay"

    # 6. Wrong transfer
    if _any_kw(complaint, _WRONG_TRANSFER_KW):
        return "wrong_transfer"

    # 7. Payment failed
    if _any_kw(complaint, _PAYMENT_FAILED_KW):
        return "payment_failed"

    # 8. Refund request
    if _any_kw(complaint, _REFUND_REQUEST_KW):
        return "refund_request"

    # 9. Other (vague, account issues, limit queries, cashback, etc.)
    return "other"


def classify_severity(
    case_type: str,
    evidence_verdict: str,
    entities: ExtractedEntities,
    is_unauthorized: bool = False,
) -> str:
    """Determine severity per the matrix in plan/03."""

    # Critical: phishing always critical
    if case_type == "phishing_or_social_engineering":
        return "critical"

    # Critical: unauthorized account access
    if is_unauthorized:
        return "critical"

    # High: confirmed financial loss scenarios
    if evidence_verdict == "consistent":
        if case_type in ("wrong_transfer", "payment_failed", "duplicate_payment", "agent_cash_in_issue"):
            return "high"
        # High-value transactions (≥ 10,000 BDT)
        if entities.amount and entities.amount >= 10_000:
            return "high"

    # Medium: inconsistent/ambiguous disputes, merchant settlement, account blocked
    if case_type in ("merchant_settlement_delay",):
        return "medium"

    if evidence_verdict in ("inconsistent", "insufficient_data"):
        if case_type in ("wrong_transfer", "duplicate_payment", "agent_cash_in_issue"):
            return "medium"
        # Emotional / urgent vague complaint
        if case_type == "other" and entities.is_unauthorized:
            return "critical"

    # Low: refund requests (policy-based), vague other
    if case_type == "refund_request":
        return "low"

    if case_type == "other":
        return "low"

    return "medium"


def classify_human_review(
    case_type: str,
    evidence_verdict: str,
    entities: ExtractedEntities,
    severity: str,
) -> bool:
    """Determine if human review is required per plan/03 Section 4."""

    # Always require human review for:
    if case_type == "phishing_or_social_engineering":
        return True
    if entities.is_unauthorized:
        return True
    if severity == "critical":
        return True

    # Dispute cases with confirmed evidence
    if case_type in ("wrong_transfer", "agent_cash_in_issue") and evidence_verdict == "consistent":
        return True

    # Verified duplicate payment
    if case_type == "duplicate_payment" and evidence_verdict == "consistent":
        return True

    # Any inconsistent evidence (contradiction)
    if evidence_verdict == "inconsistent":
        return True

    # High-value transactions
    if entities.amount and entities.amount >= 10_000:
        return True

    # Can safely be false for:
    # - payment_failed + consistent (automated reversal flow)
    # - refund_request (policy handled)
    # - merchant_settlement_delay + consistent
    # - other / insufficient_data (awaiting clarification)
    return False


def compute_confidence(
    case_type: str,
    evidence_verdict: str,
    entities: ExtractedEntities,
    matched_transaction_notes: str = "",
) -> float:
    """Deterministic confidence score per plan/01 Section 4."""

    # Phishing with clear keywords
    if case_type == "phishing_or_social_engineering":
        return 0.95

    # Perfect single match
    if evidence_verdict == "consistent":
        if entities.counterparty and entities.amount:
            return 0.92
        if entities.amount:
            return 0.85

    # Inconsistent (established pattern, etc.)
    if evidence_verdict == "inconsistent":
        return 0.75

    # Multiple ambiguous / insufficient
    if evidence_verdict == "insufficient_data":
        if entities.has_any_entity():
            return 0.65
        return 0.55

    return 0.70


def compute_reason_codes(
    case_type: str,
    evidence_verdict: str,
    matched_notes: str,
    entities: ExtractedEntities,
) -> List[str]:
    """Generate 2-3 reason codes per plan/01 Section 4."""
    codes: List[str] = []

    if case_type == "wrong_transfer":
        codes.append("wrong_transfer_claim" if evidence_verdict == "inconsistent" else "wrong_transfer")
        if "established_recipient_pattern" in matched_notes:
            codes.extend(["established_recipient_pattern", "evidence_inconsistent"])
        elif evidence_verdict == "consistent":
            codes.append("transaction_match")
    elif case_type == "payment_failed":
        codes.append("payment_failed")
        if evidence_verdict == "consistent":
            codes.append("potential_balance_deduction")
    elif case_type == "duplicate_payment":
        codes.append("duplicate_payment")
        codes.append("biller_verification_required")
    elif case_type == "agent_cash_in_issue":
        codes.extend(["agent_cash_in", "agent_ops"])
        if "pending" in matched_notes:
            codes.append("pending_transaction")
    elif case_type == "phishing_or_social_engineering":
        codes.extend(["phishing", "credential_protection", "critical_escalation"])
    elif case_type == "merchant_settlement_delay":
        codes.extend(["merchant_settlement", "delay", "pending"])
    elif case_type == "refund_request":
        codes.extend(["refund_request", "merchant_policy_dependent"])
    else:
        if evidence_verdict == "insufficient_data":
            codes.extend(["vague_complaint", "needs_clarification"])
        else:
            codes.extend(["ambiguous_match", "needs_clarification"])

    if entities.is_unauthorized:
        codes.append("unauthorized_transaction")

    return codes[:3]  # Max 3 as per plan
