"""
AI-powered fallback parser using Claude API.

When the regex parser fails to parse a message that looks like a financial
transaction, this module sends it to Claude for structured extraction.

Results are cached by message format signature so each new format only
requires one API call, ever. Subsequent messages with the same format
are parsed using the cached extraction template.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

from anthropic import Anthropic

from .message_parser import NON_TRANSACTION_PATTERNS, ParsedTransaction, TransactionType

logger = logging.getLogger("auto-budget")

# Keywords that suggest a message is financial (even if regex didn't match)
FINANCIAL_KEYWORDS = re.compile(
    r"SAR|SR|USD|EUR|INR|AED|GBP|TRY|مبلغ|رصيد|شراء|سداد|حوالة|تسديد|خصم|إيداع|سحب",
)


def _get_format_signature(text: str) -> str:
    """Generate a format signature for a message.

    The signature captures the *structure* of the message, not the specific
    values. Messages with the same structure get the same signature.

    Strategy: take the first line (transaction type indicator), strip
    leading garbage characters, and remove variable data (codes, numbers).
    """
    first_line = text.split("\n")[0].strip()
    # Strip leading non-Arabic chars (encoding artifacts like +|, +{, +~)
    cleaned = re.sub(r"^[^\u0600-\u06FFa-zA-Z]+", "", first_line)
    # Remove variable numeric suffixes (OTP codes like :1234, :5678)
    cleaned = re.sub(r":\d{3,}$", "", cleaned)
    return cleaned


def looks_financial(text: str) -> bool:
    """Check if a message likely contains financial data worth parsing.

    Excludes messages already identified as non-transactions (OTPs, promos, etc.)
    """
    # First check: must contain financial keywords
    if not FINANCIAL_KEYWORDS.search(text):
        return False

    # Second check: must not match any known non-transaction pattern
    for pattern in NON_TRANSACTION_PATTERNS:
        if pattern.search(text):
            return False

    return True


class AIParser:
    """Claude-powered fallback parser for unrecognized message formats.

    Gracefully degrades when no API credits are available - cached results
    still work, but new formats will be skipped until credits are loaded.
    """

    def __init__(self, cache: dict[str, str] = None):
        """
        Args:
            cache: Dict mapping format_signature -> JSON parse result string.
                  Loaded from StateManager.get_ai_parse_cache().
        """
        self._client = None
        self._api_available = True
        self.cache = cache or {}
        self._new_cache_entries: dict[str, tuple[str, str]] = {}

    @property
    def client(self):
        """Lazy-load Anthropic client. Returns None if unavailable."""
        if self._client is None and self._api_available:
            try:
                self._client = Anthropic()
            except Exception:
                self._api_available = False
        return self._client

    @property
    def new_cache_entries(self) -> dict[str, tuple[str, str]]:
        """New cache entries generated during this session.
        Returns dict of signature -> (json_result, sample_text).
        """
        return self._new_cache_entries

    def parse(
        self, text: str, message_rowid: int, message_date: datetime
    ) -> Optional[ParsedTransaction]:
        """Try to parse a message using Claude API with caching.

        Args:
            text: Raw SMS message text.
            message_rowid: iMessage ROWID.
            message_date: Message timestamp.

        Returns:
            ParsedTransaction if successfully parsed, None if not a transaction.
        """
        if not looks_financial(text):
            return None

        signature = _get_format_signature(text)

        # Check cache first
        if signature in self.cache:
            cached = self.cache[signature]
            if cached == "__NOT_TRANSACTION__":
                return None
            return self._apply_cached_template(
                cached, text, message_rowid, message_date
            )

        # Call Claude API (skip if no credits/client available)
        if not self.client or not self._api_available:
            return None

        logger.info(f"AI parsing new format: '{signature[:50]}...'")
        result = self._call_claude(text)

        if result is None:
            # Not a transaction - cache this so we don't ask again
            self.cache[signature] = "__NOT_TRANSACTION__"
            self._new_cache_entries[signature] = (
                "__NOT_TRANSACTION__",
                text[:200],
            )
            return None

        # Cache the result
        result_json = json.dumps(result, ensure_ascii=False)
        self.cache[signature] = result_json
        self._new_cache_entries[signature] = (result_json, text[:200])

        return self._result_to_transaction(
            result, text, message_rowid, message_date
        )

    def _call_claude(self, text: str) -> Optional[dict]:
        """Send a message to Claude for structured extraction."""
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Parse this bank SMS message from Riyad Bank (Saudi Arabia). "
                            "Extract the financial transaction details.\n\n"
                            f"Message:\n{text}\n\n"
                            "If this is NOT a financial transaction (it's an OTP, "
                            "notification, promo, card status, etc.), respond with "
                            'exactly: {"is_transaction": false}\n\n'
                            "If it IS a financial transaction, respond with ONLY "
                            "this JSON (no markdown, no explanation):\n"
                            "{\n"
                            '  "is_transaction": true,\n'
                            '  "transaction_type": "withdrawal" or "deposit",\n'
                            '  "amount": <number>,\n'
                            '  "currency": "<3-letter code>",\n'
                            '  "merchant": "<merchant or description>",\n'
                            '  "card_last_4": "<4 digits or null>",\n'
                            '  "balance": <number or null>\n'
                            "}\n\n"
                            "Rules:\n"
                            "- withdrawal = money leaving the account (purchase, "
                            "transfer out, bill payment, fee)\n"
                            "- deposit = money entering the account (salary, refund, "
                            "reversal, incoming transfer)\n"
                            "- amount must be positive\n"
                            "- merchant should be the business name, recipient, or "
                            "description in English/original\n"
                        ),
                    }
                ],
            )

            response_text = response.content[0].text.strip()
            # Strip markdown code fences if present
            if response_text.startswith("```"):
                response_text = (
                    response_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                )

            result = json.loads(response_text)

            if not result.get("is_transaction", False):
                return None

            return result

        except Exception as e:
            logger.warning(f"AI parse failed: {e}")
            # Disable API for rest of session on auth/credit errors
            error_str = str(e).lower()
            if "credit" in error_str or "401" in error_str or "auth" in error_str:
                logger.warning("Disabling AI parser for this session (no credits)")
                self._api_available = False
            return None

    def _apply_cached_template(
        self,
        cached_json: str,
        text: str,
        message_rowid: int,
        message_date: datetime,
    ) -> Optional[ParsedTransaction]:
        """Apply a cached parse template to a new message with the same format.

        The cached result tells us the format structure (transaction type, where
        to find fields). We re-extract the actual values from this message.
        """
        try:
            template = json.loads(cached_json)
        except json.JSONDecodeError:
            return None

        # The template gives us the transaction type and structure.
        # Re-extract actual values from this specific message using the
        # same field-extraction functions from message_parser.
        from .message_parser import (
            _extract_amount,
            _extract_balance,
            _extract_card,
            _extract_merchant,
        )

        txn_type_str = template.get("transaction_type", "withdrawal")
        try:
            txn_type = TransactionType(txn_type_str)
        except ValueError:
            txn_type = TransactionType.WITHDRAWAL

        # Try regex extraction first (more accurate for this specific message)
        amount_result = _extract_amount(text)
        if amount_result:
            amount, currency = amount_result
        else:
            # Fall back to template values (less ideal but better than nothing)
            amount = template.get("amount", 0)
            currency = template.get("currency", "SAR")

        if not amount or amount <= 0:
            return None

        merchant = _extract_merchant(text) or template.get("merchant", "Unknown")
        card = _extract_card(text) or template.get("card_last_4")
        balance = _extract_balance(text)
        if balance is None:
            balance = template.get("balance")

        return ParsedTransaction(
            raw_text=text,
            transaction_type=txn_type,
            amount=float(amount),
            currency=currency,
            merchant_or_description=merchant,
            date=message_date,
            card_last_4=card,
            available_balance=float(balance) if balance else None,
            message_rowid=message_rowid,
        )

    def _result_to_transaction(
        self,
        result: dict,
        text: str,
        message_rowid: int,
        message_date: datetime,
    ) -> Optional[ParsedTransaction]:
        """Convert a Claude API result dict to a ParsedTransaction."""
        try:
            txn_type = TransactionType(
                result.get("transaction_type", "withdrawal")
            )
        except ValueError:
            txn_type = TransactionType.WITHDRAWAL

        amount = result.get("amount", 0)
        if not amount or amount <= 0:
            return None

        balance = result.get("balance")

        return ParsedTransaction(
            raw_text=text,
            transaction_type=txn_type,
            amount=float(amount),
            currency=result.get("currency", "SAR"),
            merchant_or_description=result.get("merchant", "Unknown"),
            date=message_date,
            card_last_4=result.get("card_last_4"),
            available_balance=float(balance) if balance else None,
            message_rowid=message_rowid,
        )
