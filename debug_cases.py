#!/usr/bin/env python3
"""Debug failing cases."""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.preprocessor import preprocess, extract_amount, extract_transaction_type
from src.models import TicketInput
from src.evidence_engine import run_evidence_engine, _established_recipient_pattern, _parse_ts

cases_raw = json.loads((ROOT / "Preliminary Questions and Resources" / "SUST_Preli_Sample_Cases.json").read_text())["cases"]
cases = {c["id"]: c for c in cases_raw}

# --- SAMPLE-01 ---
print("=== SAMPLE-01 ===")
c = cases["SAMPLE-01"]
inp = c["input"]
complaint = inp["complaint"]
entities = preprocess(complaint, "en")
print(f"  amount={entities.amount} counterparty={entities.counterparty} txn_type={entities.txn_type}")
ticket = TicketInput(**{k: v for k, v in inp.items()})
history = ticket.transaction_history
print(f"  history TXN-9101 counterparty={history[0].counterparty}, ts={history[0].timestamp}")
# Check established recipient
t0 = history[0]
t0_ts = _parse_ts(t0.timestamp)
established = _established_recipient_pattern(history, t0.counterparty, t0_ts)
print(f"  established_recipient: {established}  (history len={len(history)})")

print()

# --- SAMPLE-04 ---
print("=== SAMPLE-04 ===")
c = cases["SAMPLE-04"]
inp = c["input"]
complaint = inp["complaint"]
entities = preprocess(complaint, "en")
print(f"  amount={entities.amount} txn_type={entities.txn_type} is_phishing={entities.is_phishing}")
ticket = TicketInput(**{k: v for k, v in inp.items()})
history = ticket.transaction_history
from src.classifier import classify_case_type
ct = classify_case_type(complaint, entities, "customer")
print(f"  case_type={ct}")
evidence = run_evidence_engine(entities, history, ct)
print(f"  evidence_verdict={evidence.evidence_verdict} relevant_txn={evidence.relevant_transaction_id} notes={evidence.notes}")

print()

# --- SAMPLE-07 ---
print("=== SAMPLE-07 ===")
c = cases["SAMPLE-07"]
inp = c["input"]
complaint = inp["complaint"]
print(f"  complaint: {complaint[:60]}")
entities = preprocess(complaint, "bn")
print(f"  amount={entities.amount} txn_type={entities.txn_type}")
ticket = TicketInput(**{k: v for k, v in inp.items()})
history = ticket.transaction_history
print(f"  TXN-9701: amount={history[0].amount} type={history[0].type} status={history[0].status}")
ct = classify_case_type(complaint, entities, "customer")
print(f"  case_type={ct}")
evidence = run_evidence_engine(entities, history, ct)
print(f"  evidence_verdict={evidence.evidence_verdict} relevant_txn={evidence.relevant_transaction_id} notes={evidence.notes}")

print()

# --- SAMPLE-08 ---
print("=== SAMPLE-08 ===")
c = cases["SAMPLE-08"]
inp = c["input"]
complaint = inp["complaint"]
print(f"  complaint: {complaint}")
entities = preprocess(complaint, "en")
print(f"  amount={entities.amount} txn_type={entities.txn_type}")
ct = classify_case_type(complaint, entities, "customer")
print(f"  case_type={ct}")

print()

# --- SAMPLE-09 ---
print("=== SAMPLE-09 ===")
c = cases["SAMPLE-09"]
inp = c["input"]
complaint = inp["complaint"]
print(f"  complaint: {complaint}")
entities = preprocess(complaint, inp.get("language", "en"))
print(f"  amount={entities.amount} txn_type={entities.txn_type}")
ticket = TicketInput(**{k: v for k, v in inp.items()})
history = ticket.transaction_history
print(f"  TXN-9901: amount={history[0].amount} type={history[0].type}")
ct = classify_case_type(complaint, entities, "merchant")
print(f"  case_type={ct}")
evidence = run_evidence_engine(entities, history, ct)
print(f"  evidence_verdict={evidence.evidence_verdict} relevant_txn={evidence.relevant_transaction_id} notes={evidence.notes}")
