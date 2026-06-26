# Part 5: Multilingual Bangla/Banglish Handling

This document details the strategy for handling tickets written in English, Bangla, and mixed "Banglish" (Bangla words written in the Latin alphabet).

---

## 1. Language Detection Strategy

If the optional `language` field is omitted from the request, the API must detect the language of the complaint using a lightweight check:

```python
def detect_language(text: str) -> str:
    # Check for Bangla unicode characters range
    bangla_char_count = sum(1 for char in text if '\u0980' <= char <= '\u09FF')
    total_chars = len(text.strip())
    
    if total_chars == 0:
        return "en"
        
    bangla_ratio = bangla_char_count / total_chars
    
    if bangla_ratio > 0.15:
        return "bn"
    elif bangla_ratio > 0.0:
        return "mixed"
    return "en"
```

---

## 2. Bangla Numeral and Keyword Normalization

Before running matching rules or passing values to the entity extractor, the API normalizes numbers and spellings:

### A. Number Normalization Mapping
```python
BANGLA_DIGIT_MAP = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

def normalize_bangla_digits(text: str) -> str:
    return text.translate(BANGLA_DIGIT_MAP)
```
*Example*: `"আমি এজেন্টের কাছে ২০০০ টাকা ক্যাশ ইন করেছি"` $\rightarrow$ `"আমি এজেন্টের কাছে 2000 টাকা ক্যাশ ইন করেছি"`.

### B. Amount Keyword Normalization
Standardize common colloquial terms representing amounts:
*   `হাজার` / `হাজার টাকা` / `K` / `k` / `hazar` $\rightarrow$ Multiply by 1000.
*   `শ` / `শত` / `sho` / `shot` $\rightarrow$ Multiply by 100.

---

## 3. Bilingual Response Policy

To ensure maximum score on **Response Quality** (10/10 points), the API adheres to the following language output rules:

1.  **`customer_reply` Language Matching**:
    *   If the input is detected as **Bangla** (`bn`): reply in **Bangla**.
    *   If the input is detected as **Mixed** (`mixed`): reply in **Bangla** (default to the local language for better user experience, since mixed speakers are typically Bangla-primary).
    *   If the input is detected as **English** (`en`): reply in **English**.
2.  **Internal Fields** (`agent_summary`, `recommended_next_action`):
    *   **Must always be in English** regardless of complaint language, to maintain standard operational logs.
3.  **Banglish (Bangla written in Roman script) Detection**:
    *   Some customers write Bangla phonetically in English letters (e.g., `"ami 2000 taka pathiechi bhai k kinto se paini"`). These are detected by the LLM's semantic understanding, not by the Unicode-based detector.
    *   These should be treated as `language = "mixed"` and receive a Bangla reply.
    *   *Tip*: The LLM prompt should explicitly instruct: "If the complaint appears to be Bangla written in Roman script, reply in Bangla."

---

## 4. Multi-Language Safe Templates Pool

For deterministic fallbacks and safety filter overrides, we define a dictionary of pre-translated bilingual safe templates covering all case types:

### Wrong Transfer Dispute
*   **EN**: `"We have noted your concern about transaction {txn_id}. Please do not share your PIN or OTP with anyone. Our dispute team will review the case and contact you through official support channels."`
*   **BN**: `"আপনার লেনদেন {txn_id} এর বিষয়ে আমরা অবগত হয়েছি। অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না। আমাদের ডিসপিউট দল বিষয়টি পর্যালোচনা করে অফিশিয়াল চ্যানেলে আপনার সাথে যোগাযোগ করবে।"`

### Failed Payment / Duplicate Payment
*   **EN**: `"We have noted that transaction {txn_id} may have caused an unexpected balance deduction. Our payments team will review the case and any eligible amount will be returned through official channels. Please do not share your PIN or OTP with anyone."`
*   **BN**: `"আমরা দেখেছি যে লেনদেন {txn_id} এর কারণে আপনার ব্যালেন্স থেকে টাকা কাটে নেওয়া হয়েছে। আমাদের পেমেন্ট টিম এটি দ্রুত যাচাই করবে এবং কোনো যোগ্য পরিমাণ অর্থ অফিশিয়াল চ্যানেলের মাধ্যমে ফেরত দেওয়া হবে। অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"`

### Phishing / Safety Alert
*   **EN**: `"Thank you for reaching out before sharing any information. We never ask for your PIN, OTP, or password under any circumstances. Please do not share these with anyone, even if they claim to be from us. Our fraud team has been notified."`
*   **BN**: `"কোনো তথ্য শেয়ার করার আগে আমাদের সাথে যোগাযোগ করার জন্য ধন্যবাদ। আমরা কোনো অবস্থাতেই আপনার পিন, ওটিপি বা পাসওয়ার্ড জিজ্ঞাসা করি না। অনুগ্রহ করে কারো সাথে এগুলো শেয়ার করবেন না, এমনকি তারা আমাদের লোক দাবি করলেও। আমাদের ফ্রড টিমকে বিষয়টি জানানো হয়েছে।"`

### Refund Request (Policy-Based)
*   **EN**: `"Thank you for reaching out. Refunds for completed merchant payments depend on the merchant's own policy. We recommend contacting the merchant directly through their official channels. If you need help, please reply and we will guide you. Please do not share your PIN or OTP with anyone."`
*   **BN**: `"যোগাযোগ করার জন্য ধন্যবাদ। সম্পন্ন মার্চেন্ট পেমেন্টের রিফান্ড নির্ভর করে মার্চেন্টের নিজস্ব নীতির উপর। আমরা সার্ভিস পাবার বিষয়ে আপনাকে সাহায্য করর বিষয়ে মার্চেন্টের সাথে যোগাযোগ করার পরামর্শ দিচ্ছি। অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"`

### Vague / Other (Needs Clarification)
*   **EN**: `"Thank you for reaching out. To help you faster, please share the transaction ID, the amount involved, and a short description of what went wrong. Please do not share your PIN or OTP with anyone."`
*   **BN**: `"যোগাযোগ করার জন্য ধন্যবাদ। আপনাকে দ্রুত সাহায্য করতে, অনুগ্রহ করে লেনদেন আইডি, জড়িত পরিমাণ এবং সমস্যার সংক্ষিপ্ত বিবরণ শেয়ার করুন। অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"`

### Agent Cash-In Issue
*   **EN**: `"We have noted your concern about the cash-in transaction {txn_id}. Our agent operations team will investigate and update you through official channels. Please do not share your PIN or OTP with anyone."`
*   **BN**: `"আপনার লেনদেন {txn_id} এর বিষয়ে আমরা অবগত হয়েছি। আমাদের এজেন্ট অপারেশন্স দল এটি দ্রুত যাচাই করবে এবং অফিশিয়াল চ্যানেলে আপনাকে জানাবে। অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"`

### Merchant Settlement Delay
*   **EN**: `"We have noted your concern about settlement {txn_id}. Our merchant operations team will check the batch status and update you on the expected settlement time through official channels."`
*   **BN**: `"সেটেলমেন্ট {txn_id} এর বিষয়ে আমরা অবগত হয়েছি। আমাদের মার্চেন্ট অপারেশন্স দল ব্যাচ স্ট্যাটাস যাচাই করে প্রত্যাশিত সেটেলমেন্ট সময় অফিশিয়াল চ্যানেলে জানাবে।"`
