"""
Department routing for QueueStorm Investigator.

Implements the matrix defined in plan/03_classification_routing.md:

  fraud_risk          <-> phishing_or_social_engineering
  dispute_resolution  <-> wrong_transfer
                        + contested refund_request
  payments_ops        <-> payment_failed | duplicate_payment
  merchant_operations <-> merchant_settlement_delay
                        + any user_type == 'merchant' complaint
  agent_operations    <-> agent_cash_in_issue
                        + any user_type == 'agent' complaint
  customer_support    <-> refund_request (undisputed)
                        + other
                        + insufficient_data (fallback for clarification)
"""
from __future__ import annotations

from typing import Any, Mapping


CONTESTED_REFUND_MARKERS = (
    "unauthorized",
    "didn't make",
    "didnt make",
    "never made",
    "someone used",
    "fraud",
    "scam",
    "chargeback",
    "stolen",
    "hacked",
    "অনুমতি ছাড়া",
    "আমি করিনি",
    "প্রতারণা",
)


def is_contested_refund(complaint: str, evidence_verdict: str) -> bool:
    """A refund_request is 'contested' if the customer disputes the charge."""
    if evidence_verdict == "inconsistent":
        return True
    text = (complaint or "").lower()
    return any(marker in text for marker in CONTESTED_REFUND_MARKERS)


def route_department(
    case_type: str,
    evidence_verdict: str,
    user_type: str = "customer",
    complaint: str = "",
) -> str:
    """Return the department enum for a classified ticket."""

    ct = (case_type or "").strip()
    ev = (evidence_verdict or "").strip()
    # Missing / None / "unknown" all collapse to "customer" so the system
    # never accidentally assumes merchant or agent persona for a vague user.
    raw_ut = (user_type or "").strip().lower()
    ut = "customer" if raw_ut in ("", "unknown", "none", "null") else raw_ut

    # 1. Safety / fraud has highest priority.
    if ct == "phishing_or_social_engineering":
        return "fraud_risk"

    # 2. Insufficient data falls back to customer_support so we can ask
    #    for clarification (except fraud, already handled above, and
    #    case types whose department is already determined by the
    #    classification - e.g. wrong_transfer still goes to dispute).
    #    merchant/agent personas also keep their default department.
    if ev == "insufficient_data" and ct in ("", "other", "refund_request") and ut in ("customer", "unknown", ""):
        return "customer_support"

    # 3. Specific case_type wins over the user_type default. A merchant
    #    reporting `payment_failed` (a customer's payment to them failed)
    #    still goes to payments_ops because that's the more specific team.
    if ct in ("payment_failed", "duplicate_payment"):
        return "payments_ops"
    if ct == "merchant_settlement_delay":
        return "merchant_operations"
    if ct == "agent_cash_in_issue":
        return "agent_operations"
    if ct == "wrong_transfer":
        return "dispute_resolution"
    if ct == "refund_request":
        return "dispute_resolution" if is_contested_refund(complaint, ev) else "customer_support"

    # 4. No specific case_type -> user_type default.
    if ut == "merchant":
        return "merchant_operations"
    if ut == "agent":
        return "agent_operations"

    # 5. Fallback.
    return "customer_support"


__all__ = ["route_department", "is_contested_refund"]


if __name__ == "__main__":  # pragma: no cover
    import json
    import pathlib
    import sys

    samples_path = pathlib.Path(__file__).resolve().parent.parent / (
        "Preliminary Questions and Resources/SUST_Preli_Sample_Cases.json"
    )

    cases = json.loads(samples_path.read_text(encoding="utf-8"))["cases"]
    failures = 0
    for case in cases:
        out = route_department(
            case_type=case["expected_output"]["case_type"],
            evidence_verdict=case["expected_output"]["evidence_verdict"],
            user_type=case["input"].get("user_type", "customer"),
            complaint=case["input"].get("complaint", ""),
        )
        expected = case["expected_output"]["department"]
        ok = out == expected
        marker = "OK " if ok else "FAIL"
        print(f"{marker} {case['id']}: routed={out!r:25s} expected={expected!r}")
        if not ok:
            failures += 1

    # user_type variation assertions
    persona_checks = [
        ("customer", "wrong_transfer", "consistent", "", "dispute_resolution"),
        ("merchant", "wrong_transfer", "consistent", "", "dispute_resolution"),
        ("merchant", "other", "insufficient_data", "", "merchant_operations"),
        ("merchant", "payment_failed", "consistent", "", "payments_ops"),
        ("agent", "other", "consistent", "agent cheated me on cash out", "agent_operations"),
        ("agent", "agent_cash_in_issue", "consistent", "", "agent_operations"),
        ("customer", "merchant_settlement_delay", "consistent", "", "merchant_operations"),
        ("merchant", "phishing_or_social_engineering", "insufficient_data", "they asked for my otp", "fraud_risk"),
        ("unknown", "wrong_transfer", "consistent", "", "dispute_resolution"),
        ("unknown", "other", "insufficient_data", "vague complaint", "customer_support"),
        # Vague user (unknown / missing) -> never assume merchant/agent.
        ("unknown", "other", "insufficient_data", "I lost money. Please help.", "customer_support"),
        ("unknown", "other", "insufficient_data", "Something is wrong with my account.", "customer_support"),
        ("", "other", "insufficient_data", "vague complaint", "customer_support"),
        ("unknown", "merchant_settlement_delay", "consistent", "My settlement has not arrived.", "merchant_operations"),
        ("unknown", "payment_failed", "consistent", "recharge failed but money gone", "payments_ops"),
        ("unknown", "phishing_or_social_engineering", "insufficient_data", "they asked for my otp", "fraud_risk"),
    ]
    for ut, ct, ev, txt, expected in persona_checks:
        out = route_department(case_type=ct, evidence_verdict=ev, user_type=ut, complaint=txt)
        ok = out == expected
        marker = "OK " if ok else "FAIL"
        print(f"{marker} persona ut={ut!r:10s} ct={ct!r:30s} -> routed={out!r:25s} expected={expected!r}")
        if not ok:
            failures += 1

    if failures:
        print(f"\n{failures} routing check(s) failed.")
        sys.exit(1)
    print("\nAll routing checks passed.")

    if failures:
        sys.exit(1)