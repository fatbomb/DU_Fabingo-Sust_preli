#!/usr/bin/env python3
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.preprocessor import preprocess
from src.models import TicketInput
from src.evidence_engine import run_evidence_engine, _match_candidates, _amount_match
from src.classifier import classify_case_type

cases_raw = json.loads((ROOT / "Preliminary Questions and Resources" / "SUST_Preli_Sample_Cases.json").read_text())["cases"]
cases = {c["id"]: c for c in cases_raw}

print("=== SAMPLE-03 ===")
c = cases["SAMPLE-03"]
inp = c["input"]
complaint = inp["complaint"]
entities = preprocess(complaint, "en")
print(f"entities: amount={entities.amount} counterparty={entities.counterparty} txn_type={entities.txn_type}")
ticket = TicketInput(**{k: v for k, v in inp.items()})
history = ticket.transaction_history
ct = classify_case_type(complaint, entities, "customer")
print(f"case_type={ct}")
for t in history:
    matches_amount = _amount_match(t.amount, entities.amount) if entities.amount else True
    matches_type = (t.type == entities.txn_type) if entities.txn_type else True
    print(f"  TXN {t.transaction_id}: amount={t.amount} type={t.type}")
    print(f"    amount_match={matches_amount} type_match={matches_type} (extracted txn_type={entities.txn_type})")
evidence = run_evidence_engine(entities, history, ct)
print(f"evidence: verdict={evidence.evidence_verdict} txn={evidence.relevant_transaction_id} notes={evidence.notes}")
