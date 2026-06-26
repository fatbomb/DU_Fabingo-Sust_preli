#!/usr/bin/env python3
"""
Test script to verify if the LLM API is working.
"""
import sys
import pathlib
import os

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.llm_client import call_llm

print("Checking Gemini API configuration...")
api_key = os.environ.get("GEMINI_API_KEY", "")

if not api_key or api_key == "your_gemini_api_key_here":
    print("⚠️ GEMINI_API_KEY is not set or is using the placeholder.")
    print("The system is currently defaulting to the robust rule-based fallback engine (Option C).")
else:
    print(f"API Key found (starts with: {api_key[:5]}...)")
    print("Attempting to call Gemini API...")
    
    result = call_llm(
        complaint="I sent 500 taka to the wrong number. Please help.",
        language="en",
        case_type="wrong_transfer",
        evidence_verdict="consistent",
        relevant_txn_id="TXN-12345",
        user_type="customer"
    )
    
    if result.success:
        print("\n✅ LLM API is working perfectly!")
        print(f"Agent Summary: {result.agent_summary}")
        print(f"Customer Reply: {result.customer_reply}")
    else:
        print("\n❌ LLM API call failed (timeout, network error, or invalid key).")
        print("The system gracefully fell back to the rule-based engine.")
