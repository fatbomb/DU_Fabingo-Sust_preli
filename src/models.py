"""
Pydantic schemas for QueueStorm Investigator.
Input and output models exactly match the JSON contract defined in the problem statement.
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Input Models
# ---------------------------------------------------------------------------

class Transaction(BaseModel):
    transaction_id: str
    timestamp: str  # ISO-8601 string; parsed by evidence engine as needed
    type: Literal["transfer", "payment", "cash_in", "cash_out", "settlement", "refund"]
    amount: float
    counterparty: Optional[str] = None
    status: Literal["completed", "failed", "pending", "reversed"]


class TicketInput(BaseModel):
    ticket_id: str
    complaint: str
    language: Optional[Literal["en", "bn", "mixed"]] = "mixed"
    channel: Optional[Literal["in_app_chat", "call_center", "email", "merchant_portal", "field_agent"]] = None
    user_type: Optional[Literal["customer", "merchant", "agent", "unknown"]] = "customer"
    campaign_context: Optional[str] = None
    transaction_history: Optional[List[Transaction]] = Field(default_factory=list)
    metadata: Optional[Any] = None

    @field_validator("complaint")
    @classmethod
    def complaint_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("complaint must not be empty")
        return v

    @field_validator("transaction_history", mode="before")
    @classmethod
    def default_empty_history(cls, v: Any) -> Any:
        return v if v is not None else []

    @field_validator("language", mode="before")
    @classmethod
    def default_language(cls, v: Any) -> str:
        if v is None or str(v).strip() == "":
            return "mixed"
        return v

    @field_validator("user_type", mode="before")
    @classmethod
    def default_user_type(cls, v: Any) -> str:
        if v is None or str(v).strip().lower() in ("", "none", "null"):
            return "customer"
        return v


# ---------------------------------------------------------------------------
# Output Models
# ---------------------------------------------------------------------------

class TicketOutput(BaseModel):
    ticket_id: str
    relevant_transaction_id: Optional[str]
    evidence_verdict: Literal["consistent", "inconsistent", "insufficient_data"]
    case_type: Literal[
        "wrong_transfer",
        "payment_failed",
        "refund_request",
        "duplicate_payment",
        "merchant_settlement_delay",
        "agent_cash_in_issue",
        "phishing_or_social_engineering",
        "other",
    ]
    severity: Literal["low", "medium", "high", "critical"]
    department: Literal[
        "customer_support",
        "dispute_resolution",
        "payments_ops",
        "merchant_operations",
        "agent_operations",
        "fraud_risk",
    ]
    agent_summary: str
    recommended_next_action: str
    customer_reply: str
    human_review_required: bool
    confidence: Optional[float] = None
    reason_codes: Optional[List[str]] = None
