"""
Transaction categorizer with rule-based and AI modes.

Works in two modes:
1. Offline (default): Rule-based keyword matching - no API needed
2. Online: Claude API for unmatched merchants - requires API credits

The rule-based system handles common merchants out of the box.
When Claude is available, it categorizes unknown merchants and the
results are cached locally so future runs don't need the API.
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger("auto-budget")

CATEGORIES = [
    "Groceries",
    "Dining",
    "Transport",
    "Shopping",
    "Entertainment",
    "Utilities",
    "Healthcare",
    "Education",
    "Subscriptions",
    "Transfer",
    "ATM",
    "Salary",
    "Other",
]

# Comprehensive keyword rules for rule-based categorization.
# Each category maps to a list of lowercase patterns to match
# against the merchant name.
KEYWORD_RULES: dict[str, list[str]] = {
    "Dining": [
        "hungerstation", "hunger station", "keeta", "jahez",
        "talabat", "carriage", "deliveroo", "uber eat",
        "wingstop", "burger king", "mcdonald", "kfc",
        "starbucks", "cafe", "coffee", "restaurant",
        "pizza", "sushi", "grill", "kitchen", "bakery",
        "shawarma", "falafel", "food", "meal", "chef",
        "dine", "falaf", "blaban", "mazaq", "lagate",
        "sultana", "subway", "domino", "baskin", "dunkin",
        "hardee", "herfy", "kudu", "al baik", "albaik",
        "maestro", "chili", "applebee", "buffalo",
        "nando", "popeye", "wendy", "taco", "krispy",
        "big chefs", "costa", "urth caffe", "kitopi",
        "wraps", "dairy", "belban", "yammak",
        "boga", "sultan", "ripples",
    ],
    "Transport": [
        "careem", "uber", "bolt", "jeeny", "swvl",
        "taxi", "ride", "fuel", "petrol", "gas station",
        "aramco", "naft", "enoc", "parking",
        "toll", "saptco", "train", "railway", "metro",
        "airline", "airways", "flynas", "saudia",
        "flyadeal", "travel", "booking.com",
        "kaiian", "rehlah", "make my trip", "makemytrip",
        "cleartrip", "wego", "almosafer",
    ],
    "Groceries": [
        "panda", "tamimi", "danube", "lulu", "carrefour",
        "extra", "othaim", "farm", "bin dawood",
        "bindawood", "nesto", "hyper", "supermarket",
        "market", "grocery", "baqala", "freshco",
        "manuel", "spinneys",
    ],
    "Shopping": [
        "amazon", "noon", "shein", "namshi", "temu",
        "jarir", "ikea", "h&m", "zara", "nike",
        "adidas", "xcite", "extra store", "cenomi",
        "tabby", "tamara", "mall", "shop", "store",
        "home centre", "pottery barn", "crate",
        "marks & spencer", "saco", "ace hardware",
        "aliexpress", "ali express", "alibaba",
        "jmran", "rubue",
    ],
    "Entertainment": [
        "vox cinema", "muvi cinema", "amc", "netflix",
        "shahid", "spotify", "apple music", "youtube",
        "gaming", "playstation", "xbox", "steam",
        "nintendo", "cinema", "movie", "theater",
        "amusement", "theme park", "blvd world",
        "boulevard", "season", "riyadh season",
        "winter wonderland", "event",
    ],
    "Subscriptions": [
        "apple com bill", "apple.com", "google play",
        "google storage", "icloud", "microsoft",
        "adobe", "dropbox", "notion", "chatgpt",
        "openai", "sonyliv", "zee5", "mbc",
        "proton", "vpn", "antivirus", "cursor",
        "github", "subscription", "renewal",
        "bluehost", "hostinger", "godaddy",
        "cloudflare", "domain", "hosting",
        "linkedin", "google linkedin",
    ],
    "Utilities": [
        "stc", "mobily", "zain", "virgin mobile",
        "saudi electric", "electricity", "water",
        "sewage", "internet", "wifi", "fiber",
        "telecom", "bill payment", "sawa",
        "ecover", "sadad",
        "خدمات المقيمين", "absher", "muqeem",
    ],
    "Healthcare": [
        "pharmacy", "hospital", "clinic", "doctor",
        "medical", "dental", "optical", "lab",
        "nahdi", "whites", "al dawaa", "medicine",
        "health", "bupa", "tawuniya", "medgulf",
    ],
    "Education": [
        "school", "university", "college", "course",
        "udemy", "coursera", "education", "training",
        "book", "library", "academy",
    ],
    "Transfer": [
        "transfer", "western union", "sent to",
        "transferred", "حوالة", "between accounts",
        "internal transfer", "outgoing transfer",
        "incoming transfer", "sarie",
        "credit card payment", "credit card transfer",
    ],
    "ATM": [
        "atm", "cash withdrawal", "cash w/d",
        "atm withdrawal", "atm deposit",
    ],
    "Salary": [
        "salary", "payroll", "wage", "راتب",
    ],
}


def _rule_based_categorize(merchant: str) -> Optional[str]:
    """Categorize a merchant using keyword matching.

    Returns the category name or None if no match found.
    """
    merchant_lower = merchant.strip().lower()

    for category, keywords in KEYWORD_RULES.items():
        for keyword in keywords:
            if keyword in merchant_lower:
                return category

    return None


class TransactionCategorizer:
    """Categorizes transactions using rules first, Claude API as optional upgrade."""

    def __init__(self, cache: dict[str, str] = None, use_ai: bool = True):
        """
        Args:
            cache: Dict mapping merchant_name (lowercase) -> category.
            use_ai: Whether to attempt Claude API calls. Set False to
                   run fully offline with rule-based categorization only.
        """
        self.cache = cache or {}
        self.use_ai = use_ai
        self._client = None

    @property
    def client(self):
        """Lazy-load Anthropic client only when needed."""
        if self._client is None:
            try:
                from anthropic import Anthropic
                self._client = Anthropic()
            except Exception:
                self._client = None
                self.use_ai = False
        return self._client

    def categorize_single(self, merchant: str) -> str:
        """Categorize a single transaction.

        Priority: cache -> keyword rules -> Claude API -> "Other"

        Only the merchant name is sent to the Claude API -- never raw SMS
        text, card numbers, balances, or other sensitive data.
        """
        cache_key = merchant.strip().lower()

        # 1. Check cache
        if cache_key in self.cache:
            return self.cache[cache_key]

        # 2. Check keyword rules (free, instant)
        category = _rule_based_categorize(merchant)
        if category:
            self.cache[cache_key] = category
            return category

        # 3. Try Claude API if available (merchant name only)
        if self.use_ai:
            category = self._try_claude(merchant)
            if category:
                self.cache[cache_key] = category
                return category

        # 4. Fallback
        self.cache[cache_key] = "Other"
        return "Other"

    def categorize_batch(self, transactions: list[dict]) -> list[str]:
        """Categorize multiple transactions efficiently.

        Uses cache and rules first, batches remaining for Claude API.
        """
        results = [None] * len(transactions)
        uncached = []

        # Resolve from cache and keyword rules first
        for i, txn in enumerate(transactions):
            cache_key = txn["merchant"].strip().lower()

            # Check cache
            if cache_key in self.cache:
                results[i] = self.cache[cache_key]
                continue

            # Check keyword rules
            category = _rule_based_categorize(txn["merchant"])
            if category:
                self.cache[cache_key] = category
                results[i] = category
                continue

            uncached.append((i, txn))

        if not uncached:
            return results

        # Try Claude API batch for uncached merchants
        if self.use_ai:
            self._batch_claude(uncached, results)

        # Fill any remaining None results with "Other"
        for i in range(len(results)):
            if results[i] is None:
                merchant_key = transactions[i]["merchant"].strip().lower()
                self.cache[merchant_key] = "Other"
                results[i] = "Other"

        return results

    def _try_claude(self, merchant: str) -> Optional[str]:
        """Try to categorize via Claude API. Only sends the merchant name."""
        if not self.client:
            return None
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=50,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Categorize this merchant into exactly ONE of: "
                            f"{', '.join(CATEGORIES)}\n\n"
                            f"Merchant: \"{merchant}\"\n"
                            f"Country: Saudi Arabia\n\n"
                            f"Reply with ONLY the category name, nothing else."
                        ),
                    }
                ],
            )
            category = response.content[0].text.strip()
            if category in CATEGORIES:
                return category
            return None
        except Exception as e:
            logger.warning(f"Claude API unavailable for categorization: {e}")
            self.use_ai = False  # Disable for rest of session
            return None

    def _batch_claude(self, uncached: list, results: list):
        """Batch categorize uncached merchants via Claude API.

        Only merchant names are sent -- no raw SMS text, card numbers,
        balances, or other sensitive data leaves the local machine.
        """
        if not self.client:
            return

        merchant_list = "\n".join(
            f"{idx + 1}. \"{txn['merchant']}\""
            for idx, (_, txn) in enumerate(uncached)
        )

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Categorize each merchant into exactly ONE "
                            f"of these categories:\n"
                            f"{', '.join(CATEGORIES)}\n\n"
                            f"Context: These are merchants from a Saudi Arabia "
                            f"bank account. Names may be in English "
                            f"or transliterated Arabic.\n\n"
                            f"Merchants:\n{merchant_list}\n\n"
                            f"Reply with ONLY a JSON array of category strings in "
                            f"the same order. Example: [\"Groceries\", \"Dining\"]"
                        ),
                    }
                ],
            )

            response_text = response.content[0].text.strip()
            if response_text.startswith("```"):
                response_text = (
                    response_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                )

            categories = json.loads(response_text)

            for (orig_idx, txn), category in zip(uncached, categories):
                if category not in CATEGORIES:
                    category = "Other"
                results[orig_idx] = category
                self.cache[txn["merchant"].strip().lower()] = category

        except Exception as e:
            logger.warning(f"Claude API batch categorization failed: {e}")
            self.use_ai = False
