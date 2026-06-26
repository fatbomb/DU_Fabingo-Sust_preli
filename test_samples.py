#!/usr/bin/env python3
"""
Quick smoke test: validates all 10 sample cases run through the pipeline
without LLM (purely deterministic). Checks:
  - No crash
  - correct case_type
  - correct department  
  - correct evidence_verdict
  - correct relevant_transaction_id (or null)
  - customer_reply is safe (no PIN/OTP requests, no refund promises)
"""
from __future__ import annotations

import json
import pathlib
import sys
import re

# Add project root to path so we can import src.*
ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.preprocessor import preprocess
from src.classifier import classify_case_type, classify_severity, classify_human_review, compute_confidence, compute_reason_codes
from src.evidence_engine import run_evidence_engine
from src.route import route_department
from src.rule_fallback import generate_fallback_response
from src.safety import CREDENTIALS_REQUEST_RE, CREDENTIALS_BANGLA_RE, REFUND_PROMISE_RE

SAMPLES = ROOT / "Preliminary Questions and Resources" / "SUST_Preli_Sample_Cases.json"
cases = json.loads(SAMPLES.read_text(encoding="utf-8"))["cases"]

# Safety patterns
_UNSAFE_PATTERNS = [CREDENTIALS_REQUEST_RE, CREDENTIALS_BANGLA_RE, REFUND_PROMISE_RE]

failures = 0
for case in cases:
    inp = case["input"]
    exp = case["expected_output"]
    ticket_id = inp["ticket_id"]
    complaint = inp["complaint"]
    language = inp.get("language", "mixed")
    user_type = inp.get("user_type", "customer")
    history_raw = inp.get("transaction_history", [])

    # Build Transaction objects
    from src.models import TicketInput
    ticket = TicketInput(
        ticket_id=ticket_id,
        complaint=complaint,
        language=language,
        user_type=user_type,
        transaction_history=history_raw,
    )
    history = ticket.transaction_history or []

    entities = preprocess(complaint, language)
    case_type = classify_case_type(complaint, entities, user_type)
    evidence = run_evidence_engine(entities, history, case_type)
    department = route_department(
        case_type=case_type,
        evidence_verdict=evidence.evidence_verdict,
        user_type=user_type,
        complaint=complaint,
        is_unauthorized=entities.is_unauthorized,
    )
    severity = classify_severity(case_type, evidence.evidence_verdict, entities, entities.is_unauthorized)
    human_review = classify_human_review(case_type, evidence.evidence_verdict, entities, severity)
    confidence = compute_confidence(case_type, evidence.evidence_verdict, entities, evidence.notes)
    reason_codes = compute_reason_codes(case_type, evidence.evidence_verdict, evidence.notes, entities)

    result = generate_fallback_response(ticket, entities, evidence, case_type, severity, human_review, confidence, reason_codes)

    # --- Assertions ---
    ok = True
    issues = []

    if result.case_type != exp["case_type"]:
        issues.append(f"case_type: got={result.case_type!r} expected={exp['case_type']!r}")
        ok = False

    if result.department != exp["department"]:
        issues.append(f"department: got={result.department!r} expected={exp['department']!r}")
        ok = False

    if result.evidence_verdict != exp["evidence_verdict"]:
        issues.append(f"evidence_verdict: got={result.evidence_verdict!r} expected={exp['evidence_verdict']!r}")
        ok = False

    if result.relevant_transaction_id != exp["relevant_transaction_id"]:
        issues.append(f"relevant_txn_id: got={result.relevant_transaction_id!r} expected={exp['relevant_transaction_id']!r}")
        ok = False

    # Safety checks
    for pat in _UNSAFE_PATTERNS:
        if pat.search(result.customer_reply):
            issues.append(f"SAFETY VIOLATION in customer_reply: pattern={pat.pattern[:40]!r}")
            ok = False

    marker = "✅ PASS" if ok else "❌ FAIL"
    print(f"{marker} {case['id']:12s} {case['label']}")
    for issue in issues:
        print(f"       └─ {issue}")

    if not ok:
        failures += 1

print(f"\n{'─'*60}")
print(f"Results: {len(cases) - failures}/{len(cases)} passed")
if failures:
    print(f"⚠️  {failures} test(s) failed — review issues above")
    sys.exit(1)
else:
    print("🎉 All sample cases passed deterministic pipeline!")
