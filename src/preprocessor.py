"""
Preprocessor: entity extraction from raw complaint text.

Handles:
- Bangla numeral normalization  (০-৯ → 0-9)
- Bangla word-number mapping    (দুই হাজার → 2000)
- Amount extraction with ±2% tolerance support
- Phone/counterparty parsing
- Transaction type keyword mapping
- Language detection (en / bn / mixed)
- Unauthorized transaction detection
- Phishing keyword detection
"""
from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Bangla numeral → ASCII digit table
# ---------------------------------------------------------------------------
_BN_DIGIT = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# ---------------------------------------------------------------------------
# Bangla word-to-number map  (most specific first)
# ---------------------------------------------------------------------------
_WORD_NUM = [
    (r"পাঁচ\s*হাজার|৫\s*হাজার|5\s*হাজার",  5000),
    (r"চার\s*হাজার|৪\s*হাজার|4\s*হাজার",    4000),
    (r"তিন\s*হাজার|৩\s*হাজার|3\s*হাজার",    3000),
    (r"দুই?\s*হাজার|২\s*হাজার|2\s*হাজার|২০ক", 2000),
    (r"এক\s*হাজার|১\s*হাজার|1\s*হাজার",     1000),
    (r"পাঁচ\s*শ[তট]|৫\s*শ[তট]|পাঁচশত",       500),
    (r"তিন\s*শ[তট]|৩\s*শ[তট]",               300),
    (r"দুই?\s*শ[তট]|২\s*শ[তট]",              200),
]
_WORD_NUM_RE = [(re.compile(pat, re.IGNORECASE), val) for pat, val in _WORD_NUM]

# ---------------------------------------------------------------------------
# Amount regex: number followed (optionally) by currency keyword
# ---------------------------------------------------------------------------
_AMOUNT_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(?:taka|tk|bdt|টাকা|টাকায়|টাকাটা|টাকার)?",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Phone number patterns
# ---------------------------------------------------------------------------
_PHONE_RE = re.compile(
    r"(?:\+?880|0)1[3-9]\d{8}"
)

# ---------------------------------------------------------------------------
# Counterparty identifier patterns  (AGENT-xxx, MERCHANT-xxx, BILLER-xxx)
# Requires a hyphen followed by at least one alphanumeric character to avoid
# matching generic words like "merchant" or "agent" in complaint text.
_COUNTERPARTY_ID_RE = re.compile(
    r"\b(AGENT|MERCHANT|BILLER)-\w+",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Transaction type keyword mapping
# ---------------------------------------------------------------------------
_TYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    # Cash-in must come BEFORE transfer so 'এজেন্টের কাছে' doesn't fall through to transfer
    ("cash_in", [
        "cash in", "cash-in", "ক্যাশ ইন", "agent cash", "এজেন্টের কাছে",
        "এজেন্ট থেকে", "agent deposit", "এজেন্ট ক্যাশ", "cashback in",
    ]),
    ("cash_out", [
        "cash out", "cash-out", "ক্যাশ আউট", "withdraw", "তুলেছি",
    ]),
    # Settlement must come BEFORE payment so '15000 sales settled' doesn't map to payment
    ("settlement", [
        "settlement", "settle", "সেটেলমেন্ট", "sales money",
        "merchant payment", "বিক্রয়", "পেমেন্ট পাইনি",
    ]),
    ("refund", [
        "refund", "reversal", "রিফান্ড", "ফেরত পেয়েছি",
        "ফেরত চাই",
    ]),
    ("transfer", [
        "sent money", "send money", "পাঠালাম", "পাঠিয়েছি", "পাঠিয়েছে",
        "wrong number", "wrong transfer", "ভুল নম্বর", "ভুল নাম্বার",
        "brother", "sister", "friend", "relative", "transfer", "ট্রান্সফার",
        "অন্য নাম্বার", "sent to", "send to",
    ]),
    ("payment", [
        "electricity", "bill", "pay bill", "recharge", "mobile recharge",
        "merchant", "পেমেন্ট", "payment", "বিলার", "রিচার্জ",
        "বিল", "মার্চেন্ট",
    ]),
]

# ---------------------------------------------------------------------------
# Phishing keyword detection
# ---------------------------------------------------------------------------
_PHISHING_KEYWORDS = [
    r"\bthey\s+asked\s+(for\s+)?my\s+(otp|pin|password)\b",
    r"\bcalled\s+(me\s+)?saying\b",
    r"\bclaimed\s+to\s+be\s+from\b",
    r"\bimpersonat",
    r"\bsuspended?\s+(my\s+)?account\b",
    r"\bblock(ed)?\s+(my\s+)?account\b.*?(otp|pin)",
    r"\bsে\s+(আমার\s+)?পিন\s+চেয়েছে\b",
    r"\bওটিপি\s+(চেয়েছে|চাইলো|দিতে)\b",
    r"\bএকাউন্ট\s+বন্ধ\s+করবে\b",
    r"\bফোন\s+করেছে\b.*?(otp|পিন|ওটিপি)",
    # generic social engineering trigger
    r"\b(asked|asking)\s+(me\s+)?(to\s+)?(share|send|give|provide)\s+(my\s+)?(otp|pin|password)\b",
]
_PHISHING_RE = re.compile("|".join(_PHISHING_KEYWORDS), re.IGNORECASE)

# ---------------------------------------------------------------------------
# Unauthorized transaction detection (distinct from wrong transfer)
# ---------------------------------------------------------------------------
_UNAUTHORIZED_KEYWORDS = [
    r"\bi\s+never\s+(made|did|sent|initiated|authorized)\b",
    r"\bsomeone\s+(used|accessed|hacked|took over)\s+(my)?\s*account\b",
    r"\bunauthori[zs]ed\s+transaction\b",
    r"\bunknown\s+transaction\b",
    r"\bআমি\s+(এই|ওই)?\s*(ট্রান্সফার|লেনদেন)\s+করিনি\b",
    r"\bআমার\s+একাউন্ট\s+থেকে\s+কেউ\b",
    r"\bসিম\s+(পরিবর্তন|swap)\b",
]
_UNAUTHORIZED_RE = re.compile("|".join(_UNAUTHORIZED_KEYWORDS), re.IGNORECASE)

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------
_BANGLA_CHAR_RE = re.compile(r"[\u0980-\u09FF]")


def detect_language(text: str) -> str:
    """Detect language: 'bn', 'en', or 'mixed'."""
    has_bangla = bool(_BANGLA_CHAR_RE.search(text))
    # Rough ASCII word count
    ascii_words = len(re.findall(r"[a-zA-Z]+", text))
    if has_bangla and ascii_words > 3:
        return "mixed"
    if has_bangla:
        return "bn"
    return "en"


def _normalize_bn_digits(text: str) -> str:
    return text.translate(_BN_DIGIT)


def extract_amount(text: str) -> Optional[float]:
    """Extract the first plausible amount from complaint text. Returns None if none found."""
    text = _normalize_bn_digits(text)

    # 1. Try word-number patterns first (higher priority)
    for pattern, value in _WORD_NUM_RE:
        if pattern.search(text):
            return float(value)

    # 2. Numeric amount regex
    candidates = []
    for match in _AMOUNT_RE.finditer(text):
        raw = match.group(1).replace(",", "")
        try:
            val = float(raw)
            # Ignore tiny amounts (< 1) and unreasonably large ones (> 10M)
            if 1.0 <= val <= 10_000_000:
                candidates.append(val)
        except ValueError:
            pass

    # Prefer larger amounts as they're more likely to be the transaction value
    return max(candidates) if candidates else None


def extract_counterparty(text: str) -> Optional[str]:
    """Extract phone number or identifier from complaint text."""
    # AGENT/MERCHANT/BILLER IDs take priority
    m = _COUNTERPARTY_ID_RE.search(text)
    if m:
        return m.group(0).upper().replace(" ", "-")

    # Phone numbers
    m = _PHONE_RE.search(text)
    if m:
        num = m.group(0)
        # Normalize to +880XXXXXXXXXX
        num = num.lstrip("+")
        if num.startswith("880"):
            return "+" + num
        if num.startswith("0"):
            return "+880" + num[1:]
        return "+" + num

    return None


def extract_transaction_type(text: str) -> Optional[str]:
    """Return the most probable transaction type keyword from the complaint."""
    lower = text.lower()
    for txn_type, keywords in _TYPE_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return txn_type
    return None


def is_phishing_complaint(text: str) -> bool:
    """True if the complaint pattern matches social engineering / phishing."""
    return bool(_PHISHING_RE.search(text))


def is_unauthorized_transaction(text: str) -> bool:
    """True if the customer claims a transaction they did NOT initiate."""
    return bool(_UNAUTHORIZED_RE.search(text))


class ExtractedEntities:
    """Result of the preprocessor stage."""
    __slots__ = (
        "amount", "counterparty", "txn_type",
        "is_phishing", "is_unauthorized",
        "detected_language",
    )

    def __init__(
        self,
        amount: Optional[float],
        counterparty: Optional[str],
        txn_type: Optional[str],
        is_phishing: bool,
        is_unauthorized: bool,
        detected_language: str,
    ) -> None:
        self.amount = amount
        self.counterparty = counterparty
        self.txn_type = txn_type
        self.is_phishing = is_phishing
        self.is_unauthorized = is_unauthorized
        self.detected_language = detected_language

    def has_any_entity(self) -> bool:
        return any([self.amount, self.counterparty, self.txn_type])


def preprocess(complaint: str, input_language: str = "mixed") -> ExtractedEntities:
    """Full entity extraction pipeline. O(n) where n = len(complaint)."""
    detected_lang = detect_language(complaint)
    # Merge: if input says bn/en explicitly trust it, else use detected
    lang = input_language if input_language in ("en", "bn") else detected_lang

    return ExtractedEntities(
        amount=extract_amount(complaint),
        counterparty=extract_counterparty(complaint),
        txn_type=extract_transaction_type(complaint),
        is_phishing=is_phishing_complaint(complaint),
        is_unauthorized=is_unauthorized_transaction(complaint),
        detected_language=lang,
    )
