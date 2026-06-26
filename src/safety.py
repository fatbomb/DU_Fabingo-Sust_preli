"""
Safety guardrails: deterministic post-processing sanitizer.

Implements plan/04_safety_guardrails.md:
  Rule 1 – No credential requests  (regex lookbehind to avoid false positives)
  Rule 2 – No unauthorized refund promises
  Rule 3 – No third-party routing

All three rules are applied to BOTH customer_reply AND recommended_next_action.

If any rule fires, the entire field is replaced with a safe pre-validated template.
The LLM can never bypass these filters.
"""
from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Rule 1 — Credential request patterns
# ---------------------------------------------------------------------------

# Matches ASKING for credentials. Lookbehinds prevent matching warnings like
# "do NOT share your PIN" or "never give your OTP".
CREDENTIALS_REQUEST_RE = re.compile(
    r"(?<!do not )(?<!never )(?<!don't )(?<!নয় )"
    r"(ask|send|give|share|provide|enter|type|write|tell us)\s+"
    r"(your\s+)?"
    r"(pin|otp|one.time.password|password|passcode|card number|card no)",
    re.IGNORECASE,
)

CREDENTIALS_BANGLA_RE = re.compile(
    r"(পিন|ওটিপি|পাসওয়ার্ড)\s*(দিন|দিতে|দেবেন|পাঠান|বলুন|জানান)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Rule 2 — Unauthorized financial commitments
# ---------------------------------------------------------------------------

REFUND_PROMISE_RE = re.compile(
    r"\b("
    r"we will refund|we have refunded|we'll refund|we're going to refund"
    r"|your money (has been|will be) returned"
    r"|your account (has been|will be) unblocked"
    r"|transaction has been reversed"
    r"|we have credited"
    r"|refund (has been|will be) processed"
    r")\b",
    re.IGNORECASE,
)

REFUND_BANGLA_RE = re.compile(
    r"("
    r"টাকা\s+ফেরত\s+দেওয়া\s+হবে"
    r"|ফেরত\s+দিচ্ছি"
    r"|আপনার\s+রিফান্ড\s+দেওয়া\s+হয়েছে"
    r"|টাকা\s+ফেরত\s+পাবেন"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Rule 3 — Third-party routing
# ---------------------------------------------------------------------------

THIRD_PARTY_RE = re.compile(
    r"(call|contact|reach|message)\s+"
    r"(the\s+recipient|him|her|them directly|the\s+sender|this\s+number\s+[+\d])",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Safe template library (all pre-validated, no dynamic promises)
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, dict[str, str]] = {
    "credential_warning": {
        "en": (
            "Please do not share your PIN or OTP with anyone. "
            "Our team is investigating the issue."
        ),
        "bn": (
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না। "
            "আমাদের টিম এটি তদন্ত করছে।"
        ),
        "mixed": (
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না। "
            "আমাদের টিম এটি তদন্ত করছে।"
        ),
    },
    "refund_payment_failed": {
        "en": (
            "We have noted your concern. Our payments team will verify the case "
            "and any eligible amount will be returned through official channels. "
            "Please do not share your PIN or OTP with anyone."
        ),
        "bn": (
            "আপনার সমস্যাটি নথিভুক্ত করা হয়েছে। আমাদের পেমেন্ট টিম এটি দ্রুত যাচাই করবে "
            "এবং কোনো যোগ্য পরিমাণ অর্থ অফিশিয়াল চ্যানেলের মাধ্যমে ফেরত দেওয়া হবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
        "mixed": (
            "আপনার সমস্যাটি নথিভুক্ত করা হয়েছে। আমাদের পেমেন্ট টিম এটি দ্রুত যাচাই করবে "
            "এবং কোনো যোগ্য পরিমাণ অর্থ অফিশিয়াল চ্যানেলের মাধ্যমে ফেরত দেওয়া হবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
    },
    "refund_wrong_transfer": {
        "en": (
            "We have received your wrong transfer dispute request. Our dispute resolution team "
            "will review the case and contact you through official channels. "
            "Please do not share your PIN or OTP with anyone."
        ),
        "bn": (
            "আমরা ভুল লেনদেনটির জন্য আপনার অভিযোগ নথিভুক্ত করেছি। "
            "আমাদের ডিসপিউট দল বিষয়টি যাচাই করে অফিশিয়াল চ্যানেলে আপনার সাথে যোগাযোগ করবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        ),
        "mixed": (
            "আমরা ভুল লেনদেনটির জন্য আপনার অভিযোগ নথিভুক্ত করেছি। "
            "আমাদের ডিসপিউট দল বিষয়টি যাচাই করে অফিশিয়াল চ্যানেলে আপনার সাথে যোগাযোগ করবে।"
        ),
    },
    "third_party_redirect": {
        "en": (
            "Please contact our official support channels only. "
            "Do not contact any third party outside official channels."
        ),
        "bn": (
            "অনুগ্রহ করে আমাদের অফিশিয়াল হেল্পলাইনে যোগাযোগ করুন। "
            "কোনো তৃতীয় পক্ষের সাথে যোগাযোগ করবেন না।"
        ),
        "mixed": (
            "অনুগ্রহ করে আমাদের অফিশিয়াল হেল্পলাইনে যোগাযোগ করুন। "
            "কোনো তৃতীয় পক্ষের সাথে যোগাযোগ করবেন না।"
        ),
    },
}


def _get_template(key: str, lang: str) -> str:
    """Fetch a safe template in the correct language."""
    section = _TEMPLATES.get(key, {})
    return section.get(lang, section.get("en", ""))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sanitize_customer_reply(
    text: str,
    case_type: str,
    language: str,
) -> str:
    """
    Sanitize the customer_reply field.
    Returns original text if clean, or a safe replacement if any rule fires.
    """
    if not text:
        return text

    # Rule 1: Credential request
    if CREDENTIALS_REQUEST_RE.search(text) or CREDENTIALS_BANGLA_RE.search(text):
        return _get_template("credential_warning", language)

    # Rule 2: Refund promise
    if REFUND_PROMISE_RE.search(text) or REFUND_BANGLA_RE.search(text):
        if case_type in ("payment_failed", "duplicate_payment"):
            return _get_template("refund_payment_failed", language)
        if case_type in ("wrong_transfer", "agent_cash_in_issue"):
            return _get_template("refund_wrong_transfer", language)
        # Generic refund promise for other case types
        return _get_template("refund_payment_failed", language)

    # Rule 3: Third-party routing
    if THIRD_PARTY_RE.search(text):
        return _get_template("third_party_redirect", language)

    return text


def sanitize_recommended_action(text: str, department: str) -> str:
    """
    Sanitize the recommended_next_action field (internal, always English).
    Less strict than customer_reply but still removes credential requests / hard promises.
    """
    if not text:
        return text

    if CREDENTIALS_REQUEST_RE.search(text) or REFUND_PROMISE_RE.search(text):
        return (
            f"Review the case manually. Do not request credentials or make financial commitments. "
            f"Route to {department} per standard policy."
        )
    return text


def sanitize_all(
    response: dict,
    language: str,
) -> dict:
    """Apply all safety filters to the full response dict. Mutates and returns it."""
    case_type = response.get("case_type", "other")
    department = response.get("department", "customer_support")

    response["customer_reply"] = sanitize_customer_reply(
        response.get("customer_reply", ""), case_type, language
    )
    response["recommended_next_action"] = sanitize_recommended_action(
        response.get("recommended_next_action", ""), department
    )
    return response
