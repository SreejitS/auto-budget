"""
Parse Riyad Bank SMS messages into structured transaction data.

Handles Arabic-language messages from Riyad Bank (Saudi Arabia).
Extracts transaction type, amount (SAR), merchant, card number, and balance.

Message format reference (from actual iMessage data):
- شراء إنترنت = Online Purchase
- شراء عبر نقاط بيع = POS Purchase
- شراء إنترنت دولي = International Online Purchase
- شراء عبر نقاط بيع دولية = International POS Purchase
- عملية عكسية = Reversal/Refund
- بطاقة إئتمانية استرداد مبلغ = Credit Card Refund
- حوالة صادرة = Outgoing Transfer (various subtypes)
- حوالة واردة = Incoming Transfer
- نوع العملية:راتب = Salary
- سداد فاتورة = Bill Payment
- بطاقة إئتمانية تسديد = Credit Card Payment
"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class TransactionType(Enum):
    WITHDRAWAL = "withdrawal"
    DEPOSIT = "deposit"


@dataclass
class ParsedTransaction:
    """A parsed bank transaction extracted from an SMS message."""

    raw_text: str
    transaction_type: TransactionType
    amount: float  # Always positive, in SAR
    currency: str  # "SAR"
    merchant_or_description: str
    date: datetime
    card_last_4: Optional[str]
    available_balance: Optional[float]
    message_rowid: int


# --- Non-transaction message filters ---
# These messages should be skipped (OTPs, promos, card status, etc.)
NON_TRANSACTION_PATTERNS = [
    # OTP / verification codes
    re.compile(r"لا تشارك الرمز"),  # "Do not share the code"
    re.compile(r"رمز توثيق"),  # "Verification code"
    re.compile(r"كلمة مرور لمرة واحدة"),  # "One-time password"
    re.compile(r"OTP|verification.?code", re.IGNORECASE),
    # Card status changes (enabled/disabled)
    re.compile(r"حالة:\s*(?:مفعلة|إيقاف مؤقت)"),  # "Status: active/paused"
    re.compile(r"بطاقة\s*:\s*إئتمانية\s*\n.*حالة"),  # Card status notification
    # Insufficient balance
    re.compile(r"رصيد غير كافي"),  # "Insufficient balance"
    # Promotional / informational
    re.compile(r"عميلنا العزيز"),  # "Dear customer" (Arabic)
    re.compile(r"عزيزنا"),  # "Dear our..." (Arabic)
    re.compile(r"حرصًا منا"),  # "For your safety"
    re.compile(r"اشعار:\s*تم توثيق"),  # "Notification: device authenticated"
    re.compile(r"Dear Customer", re.IGNORECASE),  # English promos
    re.compile(r"You have logged in", re.IGNORECASE),  # Login notifications
    # Session/login errors
    re.compile(r"لإتمام العملية يرجى تسجيل الخروج"),  # "Please logout to complete"
    re.compile(r"لايمكن إتمام العملية"),  # "Cannot complete operation"
    # Credit card statement
    re.compile(r"إصدار كشف حساب"),  # "Statement issued"
    re.compile(r"المبلغ الأدنى المستحق"),  # "Minimum amount due"
    # Beneficiary management
    re.compile(r"تم إضافة المستفيد"),  # "Beneficiary added"
    re.compile(r"المستفيد:.*حالة", re.DOTALL),  # "Beneficiary: ... status"
    # Invoice/bill management (not payments)
    re.compile(r"فاتورة:\s*\n\s*حالة:\s*إضافة"),  # "Invoice: added"
    # Account freezing notices
    re.compile(r"تم تجميد حسابك"),  # "Your account has been frozen"
    # Card management notifications
    re.compile(r"تم اضافة البطاقة.*(?:آبل|Apple)", re.IGNORECASE),  # Apple Pay card added
    re.compile(r"تم تعيين الرقم السري"),  # PIN set
    re.compile(r"إصدار بطاقة"),  # Card issued
    re.compile(r"الرقم السري غير صحيح"),  # Wrong PIN
    re.compile(r"إلغاء تسجيل بصمة"),  # Fingerprint unregistered
    # Notification / alert (non-financial)
    re.compile(r"اشعار:\s*(?:إيقاف|تنويه|تم تعيين)"),  # "Alert: stop/notice"
    re.compile(r"انتهاء صلا\s*حية"),  # "Expiry of validity" (ID expiry)
    re.compile(r"عزيزي عميل المصرفية"),  # "Dear banking customer" (greetings)
    # Card status (بطاقة : إئتمانية with status but no transaction)
    re.compile(r"^بطاقة\s*:\s*إئتمانية\s*$", re.MULTILINE),
    # Corrupted/binary messages
    re.compile(r"NSValue|NSObject|\$classname"),  # Binary plist artifacts
    # Authentication/verification codes (additional patterns)
    re.compile(r"رمز التوثيق"),  # "Authentication code"
    # Rejected operations
    re.compile(r"عملية مرفوضة"),  # "Rejected operation"
    # Thank you / account activation
    re.compile(r"شكراً على تحديث"),  # "Thanks for updating"
    re.compile(r"تم تنشيط"),  # "Activated"
    # Branch withdrawal (rare, just "سحب فرع" with no amount)
    re.compile(r"^سحب فرع$", re.MULTILINE),
]


def _extract_amount(text: str) -> Optional[tuple[float, str]]:
    """Extract amount and currency from text.

    Handles all observed Riyad Bank formats:
        مبلغ:SAR 48.00           (colon, currency first)
        مبلغ: SAR 1574.75        (colon+space, currency first)
        مبلغ:412.60 SAR          (colon, amount first)
        مبلغ:SAR 1,050.66        (with commas)
        مبلغ: 9.99 USD           (foreign currency)
        مبلغ USD 2.49             (space only, no colon)
        مبلغ SAR 241.14           (space only, no colon)
        المبلغ:INR 11222.68      (definite article)
        مبلغ العملية: INR:40750.42 (WU format with currency:amount)
        مبلغ:2.51 AED            (other currencies)
        مبلغ:18.90 978           (numeric currency codes)
    """
    ALL_CURRENCIES = r"(?:SAR|SR|USD|EUR|GBP|INR|AED|BHD|KWD|OMR|QAR|EGP|PKR|BDT|PHP|TRY|JPY|CNY|SGD|MYR|THB|\d{3})"

    # Format 1: currency:amount (Western Union style) - مبلغ العملية: INR:40750.42
    match = re.search(
        rf"مبلغ[^:\n]*[:]\s*{ALL_CURRENCIES}\s*[:]\s*(?P<amount>[\d,]+\.?\d*)",
        text,
    )
    if match:
        curr_match = re.search(ALL_CURRENCIES, match.group(0))
        currency = curr_match.group(0) if curr_match else "SAR"
        return _parse_amount(match.group("amount")), _normalize_currency(currency)

    # Format 2: مبلغ[:/ ]SAR amount (currency before number, colon or space)
    match = re.search(
        rf"(?:مبلغ|المبلغ|إجمالي المبلغ)\s*[:\s]\s*(?P<currency>{ALL_CURRENCIES})\s+(?P<amount>[\d,]+\.?\d*)",
        text,
    )
    if match:
        return _parse_amount(match.group("amount")), _normalize_currency(match.group("currency"))

    # Format 3: مبلغ[:/ ]amount currency (amount before currency, colon or space)
    match = re.search(
        rf"(?:مبلغ|المبلغ)\s*[:\s]\s*(?P<amount>[\d,]+\.?\d*)\s+(?P<currency>{ALL_CURRENCIES})",
        text,
    )
    if match:
        return _parse_amount(match.group("amount")), _normalize_currency(match.group("currency"))

    return None


def _normalize_currency(currency: str) -> str:
    """Normalize currency codes."""
    if currency in ("SAR", "SR"):
        return "SAR"
    # ISO numeric codes
    NUMERIC_CODES = {"978": "EUR", "840": "USD", "826": "GBP", "356": "INR"}
    if currency in NUMERIC_CODES:
        return NUMERIC_CODES[currency]
    return currency


def _extract_balance(text: str) -> Optional[float]:
    """Extract available balance from text.

    Handles: رصيد:SAR 57550.28 / رصيد: SAR 61462.14 / رصيد SAR 59431.50
    """
    # SAR before amount (colon or space separator)
    match = re.search(
        r"رصيد\s*[:\s]\s*(?:SAR|SR)\s*(?P<bal>[\d,]+\.?\d*)", text
    )
    if match:
        return _parse_amount(match.group("bal"))

    # Amount before SAR
    match = re.search(
        r"رصيد\s*[:\s]\s*(?P<bal>[\d,]+\.?\d*)\s*(?:SAR|SR)", text
    )
    if match:
        return _parse_amount(match.group("bal"))

    return None


def _extract_merchant(text: str) -> str:
    """Extract merchant name from text.

    Handles both colon and space-separated formats:
        من:VOX CINEMAS     (from:)
        من VOX CINEMAS     (from - no colon)
        لدى:CAREEM RIDE    (at:)
        من: PROTON AG      (from:)
        لدى:Tabby          (at:)
        إلى: Sreejit       (to:)
        جهة: موبايلي       (entity:)
    """
    # Try "من" (from) - most common for purchases. Colon or space.
    match = re.search(r"^من\s*[:\s]\s*(?P<merchant>[^\n]+)", text, re.MULTILINE)
    if match:
        merchant = match.group("merchant").strip()
        # Skip account numbers like "199940*" or "085256*"
        if not re.match(r"^\d+\*?$", merchant):
            return merchant

    # Try "لدى" (at) - used in some purchase formats
    match = re.search(r"لدى\s*[:\s]\s*(?P<merchant>[^\n]+)", text)
    if match:
        return match.group("merchant").strip()

    # Try "إلى" (to) - for transfers. Colon or space.
    match = re.search(r"^إلى\s*[:\s]\s*(?P<merchant>[^\n]+)", text, re.MULTILINE)
    if match:
        merchant = match.group("merchant").strip()
        # Skip account numbers and comma-separated account refs
        if not re.match(r"^[\d\*,\s]+$", merchant):
            return merchant

    # Try "جهة" (entity) - for bill payments
    match = re.search(r"جهة\s*[:\s]\s*(?P<merchant>[^\n]+)", text)
    if match:
        return match.group("merchant").strip()

    # Try "عبر" (via) - for SARIE transfers
    match = re.search(r"عبر\s*[:\s]\s*(?P<merchant>[^\n]+)", text)
    if match:
        return match.group("merchant").strip()

    return ""


def _extract_card(text: str) -> Optional[str]:
    """Extract card last 4 digits.

    Handles: بطاقة:5109* / بطاقة: 5109* / رقم:5294*
    """
    match = re.search(r"(?:بطاقة|رقم)\s*[:]\s*(?P<card>\d{4})\*", text)
    if match:
        return match.group("card")
    # Also handle "بطاقة ;إئتمانية 5109*"
    match = re.search(r"بطاقة\s+[;]?[^\d]*(?P<card>\d{4})\*", text)
    if match:
        return match.group("card")
    return None


def _parse_amount(amount_str: str) -> float:
    """Parse amount string, removing commas and handling edge cases."""
    cleaned = amount_str.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _detect_transaction_type(text: str) -> Optional[tuple[TransactionType, str]]:
    """Detect transaction type from Arabic keywords in the message.

    Returns (TransactionType, type_description) or None if not a transaction.
    """
    first_lines = text[:200].lower()

    # --- WITHDRAWAL types ---

    # Online purchase (شراء إنترنت) - most common
    if re.search(r"شراء إنترنت دولي", text):
        return TransactionType.WITHDRAWAL, "International Online Purchase"
    if re.search(r"شراء إنترنت", text):
        return TransactionType.WITHDRAWAL, "Online Purchase"

    # POS purchase (شراء عبر نقاط بيع)
    if re.search(r"شراء عبر نقاط بيع دولية", text):
        return TransactionType.WITHDRAWAL, "International POS Purchase"
    if re.search(r"شراء عبر نقاط بيع", text):
        return TransactionType.WITHDRAWAL, "POS Purchase"

    # Outgoing transfer - Western Union
    if re.search(r"حوالة صادر[ةه].*ويسترن يونيون", text):
        return TransactionType.WITHDRAWAL, "Western Union Transfer"
    # Outgoing transfer - SARIE/local
    if re.search(r"حوالة صادرة مقبولة", text):
        return TransactionType.WITHDRAWAL, "Outgoing Transfer (SARIE)"
    if re.search(r"حوالة صادرة محلية", text):
        return TransactionType.WITHDRAWAL, "Outgoing Transfer (Local)"
    if re.search(r"حوالة صادرة داخلية", text):
        return TransactionType.WITHDRAWAL, "Internal Transfer (Out)"
    if re.search(r"حوالة صادرة\s*:\s*بين حساباتك", text):
        return TransactionType.WITHDRAWAL, "Transfer Between Accounts (Out)"
    if re.search(r"حوالة صادرة", text):
        return TransactionType.WITHDRAWAL, "Outgoing Transfer"

    # Bill payment (سداد فاتورة)
    if re.search(r"سداد فاتورة", text):
        return TransactionType.WITHDRAWAL, "Bill Payment"

    # Credit card payment (بطاقة إئتمانية تسديد)
    if re.search(r"(?:بطاقة إئتمانية تسديد|تأكيد السداد)", text):
        return TransactionType.WITHDRAWAL, "Credit Card Payment"

    # ATM withdrawal / deposit
    if re.search(r"سحب.*(?:صراف|ATM)", text):
        return TransactionType.WITHDRAWAL, "ATM Withdrawal"
    if re.search(r"إيداع صراف", text):
        return TransactionType.DEPOSIT, "ATM Deposit"

    # Credit card transfer (حوالة من بطاقة ائتمانية)
    if re.search(r"حوالة من بطاقة ائتمانية", text):
        return TransactionType.WITHDRAWAL, "Credit Card Transfer"

    # Credit card payment (سداد بطاقات ائتمان)
    if re.search(r"سداد بطاقات ائتمان", text):
        return TransactionType.WITHDRAWAL, "Credit Card Payment"

    # Cash back (استرجاع نقدي)
    if re.search(r"استرجاع نقدي", text):
        return TransactionType.DEPOSIT, "Cash Back"

    # Fee deduction (خصم رسوم)
    if re.search(r"خصم رسوم", text):
        return TransactionType.WITHDRAWAL, "Fee"

    # --- DEPOSIT types ---

    # Reversal / refund (عملية عكسية)
    if re.search(r"عملية عكسية", text):
        return TransactionType.DEPOSIT, "Reversal"

    # Credit card refund (استرداد مبلغ)
    if re.search(r"استرداد مبلغ", text):
        return TransactionType.DEPOSIT, "Refund"

    # Salary (راتب)
    if re.search(r"نوع العملية\s*:\s*راتب", text):
        return TransactionType.DEPOSIT, "Salary"
    if re.search(r"راتب", text):
        return TransactionType.DEPOSIT, "Salary"

    # Incoming transfer
    if re.search(r"حوالة واردة\s*:\s*بين حساباتك", text):
        return TransactionType.DEPOSIT, "Transfer Between Accounts (In)"
    if re.search(r"حوالة واردة داخلية", text):
        return TransactionType.DEPOSIT, "Internal Transfer (In)"
    if re.search(r"حوالة واردة", text):
        return TransactionType.DEPOSIT, "Incoming Transfer"

    return None


class MessageParser:
    """Parses Riyad Bank Arabic SMS messages into structured transactions."""

    def is_transaction_message(self, text: str) -> bool:
        """Check if a message is a financial transaction (not OTP, promo, etc.)."""
        for pattern in NON_TRANSACTION_PATTERNS:
            if pattern.search(text):
                return False
        return True

    def parse(
        self, text: str, message_rowid: int, message_date: datetime
    ) -> Optional[ParsedTransaction]:
        """Parse a bank SMS message into a structured transaction.

        Args:
            text: The SMS message text (Arabic).
            message_rowid: The iMessage ROWID (for deduplication).
            message_date: The message timestamp.

        Returns:
            ParsedTransaction if successfully parsed, None otherwise.
        """
        if not text:
            return None

        # Skip non-transaction messages
        if not self.is_transaction_message(text):
            return None

        # Detect transaction type
        type_result = _detect_transaction_type(text)
        if type_result is None:
            return None

        txn_type, type_desc = type_result

        # Extract amount
        amount_result = _extract_amount(text)
        if amount_result is None:
            return None
        amount, currency = amount_result
        if amount <= 0:
            return None

        # Extract other fields
        balance = _extract_balance(text)
        merchant = _extract_merchant(text)
        card = _extract_card(text)

        # Use type description as fallback merchant
        if not merchant:
            merchant = type_desc

        return ParsedTransaction(
            raw_text=text,
            transaction_type=txn_type,
            amount=amount,
            currency=currency,
            merchant_or_description=merchant,
            date=message_date,
            card_last_4=card,
            available_balance=balance,
            message_rowid=message_rowid,
        )
