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
    ut = (user_type or "customer").strip().lower()

    # 1. Safety / fraud has highest priority.
    if ct == "phishing_or_social_engineering":
        return "fraud_risk"

    # 2. Insufficient data falls back to customer_support so we can ask
    #    for clarification (except fraud, already handled above, and
    #    case types whose department is already determined by the
    #    classification - e.g. wrong_transfer still goes to dispute).
    if ev == "insufficient_data" and ct in ("", "other", "refund_request"):
        return "customer_support"

    # 3. User-type based defaults.
    if ut == "merchant":
        return "merchant_operations"
    if ut == "agent":
        return "agent_operations"

    # 4. Case-type based routing.
    if ct == "wrong_transfer":
        return "dispute_resolution"

    if ct == "refund_request":
        return "dispute_resolution" if is_contested_refund(complaint, ev) else "customer_support"

    if ct in ("payment_failed", "duplicate_payment"):
        return "payments_ops"

    if ct == "merchant_settlement_delay":
        return "merchant_operations"

    if ct == "agent_cash_in_issue":
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

    if failures:
        sys.exit(1)