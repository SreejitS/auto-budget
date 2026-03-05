"""
Live exchange rate conversion to SAR.

Fetches rates from open.er-api.com with 24-hour in-memory caching.
Falls back to hardcoded rates if the API is unreachable.
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger("auto-budget")

# Fallback rates (approximate SAR per 1 unit of foreign currency)
FALLBACK_RATES = {
    "USD": 3.75,
    "EUR": 4.10,
    "GBP": 4.75,
    "INR": 0.045,
    "AED": 1.02,
    "BHD": 9.95,
    "KWD": 12.20,
    "OMR": 9.74,
    "QAR": 1.03,
    "EGP": 0.077,
    "PKR": 0.013,
    "BDT": 0.031,
    "PHP": 0.065,
    "TRY": 0.10,
    "JPY": 0.025,
    "CNY": 0.52,
    "SGD": 2.80,
    "MYR": 0.84,
    "THB": 0.11,
}

_cache: Optional[dict] = None
_cache_time: float = 0
CACHE_TTL = 86400  # 24 hours


def _fetch_rates() -> Optional[dict]:
    """Fetch SAR-based exchange rates from API.

    Returns dict mapping currency code -> rate (how many units per 1 SAR).
    """
    global _cache, _cache_time

    if _cache and (time.time() - _cache_time) < CACHE_TTL:
        return _cache

    try:
        resp = requests.get(
            "https://open.er-api.com/v6/latest/SAR", timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("result") == "success":
            _cache = data["rates"]
            _cache_time = time.time()
            logger.debug("Exchange rates refreshed from API")
            return _cache
    except Exception as e:
        logger.warning(f"Exchange rate API failed: {e}")

    return _cache  # Return stale cache if available


def get_sar_amount(foreign_amount: float, currency: str) -> float:
    """Convert a foreign currency amount to SAR.

    Args:
        foreign_amount: Amount in the foreign currency.
        currency: 3-letter currency code (e.g. "USD").

    Returns:
        Equivalent amount in SAR.
    """
    if currency == "SAR":
        return foreign_amount

    rates = _fetch_rates()
    if rates and currency in rates:
        # rates[currency] = how many units of currency per 1 SAR
        # So SAR amount = foreign_amount / rates[currency]
        return round(foreign_amount / rates[currency], 2)

    # Fallback to hardcoded rates
    if currency in FALLBACK_RATES:
        return round(foreign_amount * FALLBACK_RATES[currency], 2)

    logger.warning(f"No exchange rate for {currency}, using 1:1")
    return foreign_amount
