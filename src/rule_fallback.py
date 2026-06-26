"""
Pure rule-based fallback engine (Option C).

Used when:
  - LLM API call fails or times out (> 15 seconds)
  - LLM response fails schema validation
  - Network is unavailable

Generates all required output fields using only:
  - Extracted entities (preprocessor)
  - Classification results (classifier)
  - Evidence results (evidence_engine)
  - Pre-validated safe templates

Latency: < 5ms. Zero external dependencies.
"""
from __future__ import annotations

from typing import Optional, List

from .models import TicketInput, TicketOutput, Transaction
from .preprocessor import ExtractedEntities
from .evidence_engine import EvidenceResult
from .route import route_department


# ---------------------------------------------------------------------------
# Agent Summary templates
# ---------------------------------------------------------------------------

def _build_agent_summary(
    entities: ExtractedEntities,
    case_type: str,
    evidence: EvidenceResult,
    ticket: TicketInput,
) -> str:
    """Build a deterministic English agent summary without LLM."""
    txn_ref = f" ({evidence.relevant_transaction_id})" if evidence.relevant_transaction_id else ""
    amount_ref = f" {entities.amount:.0f} BDT" if entities.amount else ""
    cp_ref = f" to {entities.counterparty}" if entities.counterparty else ""

    summaries = {
        "wrong_transfer": (
            f"Customer reports sending{amount_ref}{cp_ref} was a wrong transfer{txn_ref}. "
            f"Evidence verdict: {evidence.evidence_verdict}."
        ),
        "payment_failed": (
            f"Customer reports a{amount_ref} payment{txn_ref} failed but balance may have been deducted. "
            f"Requires payments operations investigation."
        ),
        "duplicate_payment": (
            f"Customer reports duplicate charge of{amount_ref}{txn_ref}. "
            f"Two identical transactions detected."
        ),
        "refund_request": (
            f"Customer requests refund of{amount_ref}{txn_ref} for completed payment. "
            f"Not a service failure — policy-based handling required."
        ),
        "merchant_settlement_delay": (
            f"Merchant reports{amount_ref} settlement{txn_ref} delayed beyond standard window."
        ),
        "agent_cash_in_issue": (
            f"Customer reports{amount_ref} cash-in via agent{txn_ref} not reflected in balance. "
            f"Transaction status: {evidence.matched_transaction.status if evidence.matched_transaction else 'unknown'}."
        ),
        "phishing_or_social_engineering": (
            "Customer reports an unsolicited contact asking for credentials. "
            "Likely social engineering or phishing attempt. Customer has been advised not to share credentials."
        ),
        "other": (
            "Customer reports a concern about their account or transaction without sufficient detail. "
            "Awaiting clarification to investigate further."
            + (f" Evidence verdict: {evidence.evidence_verdict}." if evidence.evidence_verdict == "insufficient_data" else "")
        ),
    }

    # Unauthorized transaction override
    if entities.is_unauthorized:
        return (
            f"Customer reports a transaction{txn_ref} they did not initiate. "
            f"Potential account compromise or unauthorized access. "
            f"Escalated to fraud_risk for immediate investigation."
        )

    return summaries.get(case_type, summaries["other"])


# ---------------------------------------------------------------------------
# Recommended next action templates
# ---------------------------------------------------------------------------

def _build_recommended_action(
    case_type: str,
    evidence: EvidenceResult,
    department: str,
    entities: ExtractedEntities,
) -> str:
    """Deterministic internal recommended_next_action text."""
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


# ---------------------------------------------------------------------------
# Customer reply templates (safe, pre-validated)
# ---------------------------------------------------------------------------

_CUSTOMER_REPLIES: dict[str, dict[str, str]] = {
    "wrong_transfer": {
        "en": (
            "We have noted your concern about this transaction. Please do not share your PIN or OTP "
            "with anyone. Our dispute team will review the case and contact you through official support channels."
        ),
        "bn": (
            "আপনার লেনদেন সম্পর্কিত অভিযোগটি নথিভুক্ত করা হয়েছে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না। "
            "আমাদের ডিসপিউট দল বিষয়টি পর্যালোচনা করে অফিশিয়াল চ্যানেলে আপনার সাথে যোগাযোগ করবে।"
        ),
        "mixed": (
            "আপনার লেনদেন সম্পর্কিত অভিযোগটি নথিভুক্ত করা হয়েছে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না। "
            "আমাদের ডিসপিউট দল বিষয়টি পর্যালোচনা করে অফিশিয়াল চ্যানেলে আপনার সাথে যোগাযোগ করবে।"
        ),
    },
    "payment_failed": {
        "en": (
            "We have noted that this transaction may have caused an unexpected balance deduction. "
            "Our payments team will review the case and any eligible amount will be returned through "
            "official channels. Please do not share your PIN or OTP with anyone."
        ),
        "bn": (
            "আমরা দেখেছি যে এই লেনদেনের কারণে আপনার ব্যালেন্স থেকে টাকা কেটে নেওয়া হতে পারে। "
            "আমাদের পেমেন্ট টিম এটি দ্রুত যাচাই করবে এবং কোনো যোগ্য পরিমাণ অর্থ "
            "অফিশিয়াল চ্যানেলের মাধ্যমে ফেরত দেওয়া হবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
        "mixed": (
            "আমরা দেখেছি যে এই লেনদেনের কারণে আপনার ব্যালেন্স থেকে টাকা কেটে নেওয়া হতে পারে। "
            "আমাদের পেমেন্ট টিম এটি দ্রুত যাচাই করবে এবং কোনো যোগ্য পরিমাণ অর্থ "
            "অফিশিয়াল চ্যানেলের মাধ্যমে ফেরত দেওয়া হবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
    },
    "duplicate_payment": {
        "en": (
            "We have noted the possible duplicate payment. Our payments team will verify with the "
            "biller and any eligible amount will be returned through official channels. "
            "Please do not share your PIN or OTP with anyone."
        ),
        "bn": (
            "সম্ভাব্য ডুপ্লিকেট পেমেন্টের বিষয়টি নথিভুক্ত করা হয়েছে। "
            "আমাদের পেমেন্ট টিম বিলারের সাথে যাচাই করবে এবং কোনো যোগ্য পরিমাণ অর্থ "
            "অফিশিয়াল চ্যানেলের মাধ্যমে ফেরত দেওয়া হবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
        "mixed": (
            "সম্ভাব্য ডুপ্লিকেট পেমেন্টের বিষয়টি নথিভুক্ত করা হয়েছে। "
            "আমাদের পেমেন্ট টিম বিলারের সাথে যাচাই করবে এবং কোনো যোগ্য পরিমাণ অর্থ "
            "অফিশিয়াল চ্যানেলের মাধ্যমে ফেরত দেওয়া হবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
    },
    "refund_request": {
        "en": (
            "Thank you for reaching out. Refunds for completed merchant payments depend on the merchant's "
            "own policy. We recommend contacting the merchant directly. If you need help reaching them, "
            "please reply and we will guide you. Please do not share your PIN or OTP with anyone."
        ),
        "bn": (
            "যোগাযোগ করার জন্য ধন্যবাদ। সম্পন্ন মার্চেন্ট পেমেন্টের রিফান্ড নির্ভর করে "
            "মার্চেন্টের নিজস্ব নীতির উপর। আমরা সরাসরি মার্চেন্টের সাথে যোগাযোগ করার পরামর্শ দিচ্ছি। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
        "mixed": (
            "যোগাযোগ করার জন্য ধন্যবাদ। সম্পন্ন মার্চেন্ট পেমেন্টের রিফান্ড নির্ভর করে "
            "মার্চেন্টের নিজস্ব নীতির উপর। আমরা সরাসরি মার্চেন্টের সাথে যোগাযোগ করার পরামর্শ দিচ্ছি। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
    },
    "merchant_settlement_delay": {
        "en": (
            "We have noted your concern about the settlement. Our merchant operations team will check "
            "the batch status and update you on the expected settlement time through official channels."
        ),
        "bn": (
            "সেটেলমেন্টের বিষয়ে আমরা অবগত হয়েছি। আমাদের মার্চেন্ট অপারেশন্স দল ব্যাচ স্ট্যাটাস "
            "যাচাই করে প্রত্যাশিত সেটেলমেন্ট সময় অফিশিয়াল চ্যানেলে জানাবে।"
        ),
        "mixed": (
            "সেটেলমেন্টের বিষয়ে আমরা অবগত হয়েছি। আমাদের মার্চেন্ট অপারেশন্স দল ব্যাচ স্ট্যাটাস "
            "যাচাই করে প্রত্যাশিত সেটেলমেন্ট সময় অফিশিয়াল চ্যানেলে জানাবে।"
        ),
    },
    "agent_cash_in_issue": {
        "en": (
            "We have noted your concern about this cash-in transaction. Our agent operations team "
            "will investigate and update you through official channels. "
            "Please do not share your PIN or OTP with anyone."
        ),
        "bn": (
            "আপনার ক্যাশ ইন লেনদেনের বিষয়ে আমরা অবগত হয়েছি। "
            "আমাদের এজেন্ট অপারেশন্স দল এটি দ্রুত যাচাই করবে এবং অফিশিয়াল চ্যানেলে আপনাকে জানাবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
        "mixed": (
            "আপনার ক্যাশ ইন লেনদেনের বিষয়ে আমরা অবগত হয়েছি। "
            "আমাদের এজেন্ট অপারেশন্স দল এটি দ্রুত যাচাই করবে এবং অফিশিয়াল চ্যানেলে আপনাকে জানাবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
    },
    "phishing_or_social_engineering": {
        "en": (
            "Thank you for reaching out before sharing any information. We never ask for your PIN, "
            "OTP, or password under any circumstances. Please do not share these with anyone, even "
            "if they claim to be from us. Our fraud team has been notified of this incident."
        ),
        "bn": (
            "কোনো তথ্য শেয়ার করার আগে আমাদের সাথে যোগাযোগ করার জন্য ধন্যবাদ। "
            "আমরা কোনো অবস্থাতেই আপনার পিন, ওটিপি বা পাসওয়ার্ড জিজ্ঞাসা করি না। "
            "অনুগ্রহ করে কারো সাথে এগুলো শেয়ার করবেন না, এমনকি তারা আমাদের লোক দাবি করলেও। "
            "আমাদের ফ্রড টিমকে বিষয়টি জানানো হয়েছে।"
        ),
        "mixed": (
            "কোনো তথ্য শেয়ার করার আগে আমাদের সাথে যোগাযোগ করার জন্য ধন্যবাদ। "
            "আমরা কোনো অবস্থাতেই আপনার পিন, ওটিপি বা পাসওয়ার্ড জিজ্ঞাসা করি না। "
            "অনুগ্রহ করে কারো সাথে এগুলো শেয়ার করবেন না, এমনকি তারা আমাদের লোক দাবি করলেও। "
            "আমাদের ফ্রড টিমকে বিষয়টি জানানো হয়েছে।"
        ),
    },
    "other": {
        "en": (
            "Thank you for reaching out. To help you faster, please share the transaction ID, "
            "the amount involved, and a short description of what went wrong. "
            "Please do not share your PIN or OTP with anyone."
        ),
        "bn": (
            "যোগাযোগ করার জন্য ধন্যবাদ। আপনাকে দ্রুত সাহায্য করতে, অনুগ্রহ করে লেনদেন আইডি, "
            "জড়িত পরিমাণ এবং সমস্যার সংক্ষিপ্ত বিবরণ শেয়ার করুন। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
        "mixed": (
            "যোগাযোগ করার জন্য ধন্যবাদ। আপনাকে দ্রুত সাহায্য করতে, অনুগ্রহ করে লেনদেন আইডি, "
            "জড়িত পরিমাণ এবং সমস্যার সংক্ষিপ্ত বিবরণ শেয়ার করুন। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
    },
}

_UNAUTHORIZED_REPLY = {
    "en": (
        "We have received your report about a transaction you did not initiate. "
        "Our security team has been notified and will investigate immediately. "
        "Please do not share your PIN or OTP with anyone, even if they claim to be from us."
    ),
    "bn": (
        "আপনি যে লেনদেনটি করেননি সে বিষয়ে আমরা আপনার রিপোর্ট পেয়েছি। "
        "আমাদের সিকিউরিটি টিম অবিলম্বে তদন্ত করবে। "
        "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
    "mixed": (
        "আপনি যে লেনদেনটি করেননি সে বিষয়ে আমরা আপনার রিপোর্ট পেয়েছি। "
        "আমাদের সিকিউরিটি টিম অবিলম্বে তদন্ত করবে। "
        "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
}


def _get_customer_reply(case_type: str, language: str, entities: ExtractedEntities, txn_id: Optional[str]) -> str:
    """Get pre-validated customer reply and inject transaction ID if available."""
    if entities.is_unauthorized:
        reply = _UNAUTHORIZED_REPLY.get(language, _UNAUTHORIZED_REPLY["en"])
    else:
        section = _CUSTOMER_REPLIES.get(case_type, _CUSTOMER_REPLIES["other"])
        reply = section.get(language, section.get("en", ""))

    # Inject the real transaction ID if we have it
    if txn_id:
        reply = reply.replace("this transaction", f"transaction {txn_id}")
        reply = reply.replace("this cash-in transaction", f"cash-in transaction {txn_id}")
        reply = reply.replace("the settlement", f"settlement {txn_id}")
        reply = reply.replace("this transaction", f"transaction {txn_id}")
    return reply


def generate_fallback_response(
    ticket: TicketInput,
    entities: ExtractedEntities,
    evidence: EvidenceResult,
    case_type: str,
    severity: str,
    human_review: bool,
    confidence: float,
    reason_codes: List[str],
) -> TicketOutput:
    """
    Build a complete TicketOutput using only deterministic templates.
    This is the Option C (ultimate fail-safe) from plan/01.
    """
    language = entities.detected_language

    department = route_department(
        case_type=case_type,
        evidence_verdict=evidence.evidence_verdict,
        user_type=ticket.user_type or "customer",
        complaint=ticket.complaint,
        is_unauthorized=entities.is_unauthorized,
    )

    agent_summary = _build_agent_summary(entities, case_type, evidence, ticket)
    recommended_action = _build_recommended_action(case_type, evidence, department, entities)
    customer_reply = _get_customer_reply(case_type, language, entities, evidence.relevant_transaction_id)

    return TicketOutput(
        ticket_id=ticket.ticket_id,
        relevant_transaction_id=evidence.relevant_transaction_id,
        evidence_verdict=evidence.evidence_verdict,
        case_type=case_type,
        severity=severity,
        department=department,
        agent_summary=agent_summary,
        recommended_next_action=recommended_action,
        customer_reply=customer_reply,
        human_review_required=human_review,
        confidence=round(confidence, 2),
        reason_codes=reason_codes,
    )
