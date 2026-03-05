"""
Auto-budget REST API for Raspberry Pi.

Receives pre-parsed transaction data from iPhone Shortcuts,
categorizes merchants, and pushes to Firefly III.
"""

import hashlib
import logging
import os
import sys
from datetime import datetime
from functools import wraps
from pathlib import Path

import yaml
from dotenv import load_dotenv
from flask import Flask, jsonify, request

# Add project root to path so we can import shared modules
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.categorizer import TransactionCategorizer
from shared.exchange_rates import get_sar_amount
from shared.firefly_client import FireflyClient
from shared.state import StateManager
from mac.src.message_parser import MessageParser

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("auto-budget-api")

# --- Globals (initialized lazily on first request) ---
_state: StateManager = None
_categorizer: TransactionCategorizer = None
_firefly: FireflyClient = None
_config: dict = None
_api_key: str = None


def _init():
    """Initialize all components. Called once on first request."""
    global _state, _categorizer, _firefly, _config, _api_key

    # Load .env from server/ directory
    server_dir = Path(__file__).parent.parent
    load_dotenv(server_dir / ".env")

    # Load config
    config_path = os.environ.get(
        "CONFIG_PATH",
        str(PROJECT_ROOT / "config" / "config.yaml"),
    )
    with open(config_path) as f:
        _config = yaml.safe_load(f)

    # State DB
    db_path = os.environ.get(
        "STATE_DB_PATH",
        str(server_dir / "data" / "auto-budget.db"),
    )
    _state = StateManager(db_path)

    # Categorizer with merchant cache
    _categorizer = TransactionCategorizer(
        cache=_state.get_merchant_cache(),
    )

    # Firefly III client
    firefly_token = os.environ.get(
        "FIREFLY_API_TOKEN",
        _config.get("firefly", {}).get("api_token", ""),
    )
    firefly_url = _config.get("firefly", {}).get("base_url", "http://localhost:8080")
    _firefly = FireflyClient(base_url=firefly_url, token=firefly_token)

    # API key for authentication
    _api_key = os.environ.get("AUTO_BUDGET_API_KEY", "")
    if not _api_key:
        logger.warning("AUTO_BUDGET_API_KEY not set — API is open to all requests")


def require_api_key(f):
    """Decorator: require X-API-Key header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _api_key:
            return f(*args, **kwargs)
        key = request.headers.get("X-API-Key", "")
        if key != _api_key:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def _generate_external_id(date_str: str, amount: float, merchant: str) -> str:
    """Generate deterministic external_id for deduplication.

    Source-agnostic: same transaction from iPhone and Mac produces the same ID.
    """
    raw = f"{date_str[:10]}|{amount:.2f}|{merchant.strip().lower()}"
    return f"txn_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


@app.before_request
def ensure_init():
    if _state is None:
        _init()


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    firefly_ok = False
    if _firefly:
        try:
            firefly_ok = _firefly.test_connection()
        except Exception:
            pass

    return jsonify({
        "status": "ok",
        "firefly_connected": firefly_ok,
        "merchant_cache_size": len(_categorizer.cache) if _categorizer else 0,
    })


@app.route("/api/transaction", methods=["POST"])
@require_api_key
def create_transaction():
    """
    Accept a pre-parsed transaction from iPhone Shortcuts or Mac.

    JSON body:
    {
        "amount": 48.00,
        "currency": "SAR",
        "merchant": "McDonald's",
        "type": "withdrawal",
        "date": "2026-03-02T14:30:00",
        "source": "iphone"
    }

    Only safe fields — no card numbers, OTPs, or balances.
    """
    data = request.get_json(force=True)

    # Validate required fields
    required = ["amount", "merchant", "type"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({"error": f"missing fields: {missing}"}), 400

    try:
        amount = float(str(data["amount"]).replace(",", ""))
    except (ValueError, TypeError):
        return jsonify({"error": "invalid amount"}), 400

    if amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400

    currency = data.get("currency", "SAR")
    merchant = str(data["merchant"]).strip()
    txn_type = data["type"]
    date_str = data.get("date", datetime.now().isoformat())
    source = data.get("source", "iphone")

    if txn_type not in ("withdrawal", "deposit", "transfer"):
        return jsonify({"error": "type must be 'withdrawal', 'deposit', or 'transfer'"}), 400

    if not merchant:
        return jsonify({"error": "merchant cannot be empty"}), 400

    # Parse date
    try:
        txn_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        txn_date = datetime.now()

    # Generate dedup ID
    external_id = _generate_external_id(date_str, amount, merchant)

    # Check if already processed locally
    if _state.is_api_transaction_processed(external_id):
        return jsonify({
            "status": "duplicate",
            "category": None,
            "external_id": external_id,
        }), 200

    # Categorize merchant
    category = _categorizer.categorize_single(merchant)
    _state.update_merchant_cache(_categorizer.cache)

    # Build Firefly III transaction
    asset_account = _config.get("firefly", {}).get(
        "asset_account_name", "Riyad Bank Account"
    )
    cc_account = _config.get("firefly", {}).get(
        "credit_card_account_name", "Riyad Bank Credit Card"
    )

    if txn_type == "transfer":
        source_name = asset_account
        destination_name = cc_account
    elif txn_type == "withdrawal":
        source_name = asset_account
        destination_name = merchant
    else:
        source_name = merchant
        destination_name = asset_account

    # Handle foreign currency
    firefly_amount = amount
    firefly_currency = "SAR"
    foreign_amount = None
    foreign_currency_code = None

    if currency != "SAR":
        foreign_amount = amount
        foreign_currency_code = currency
        firefly_amount = get_sar_amount(amount, currency)
        firefly_currency = "SAR"

    try:
        result = _firefly.create_transaction(
            transaction_type=txn_type,
            amount=firefly_amount,
            description=merchant,
            date=txn_date,
            source_name=source_name,
            destination_name=destination_name,
            category_name=category,
            currency_code=firefly_currency,
            notes=f"Auto-imported from {source}",
            external_id=external_id,
            tags=["auto-imported", f"source:{source}"],
            foreign_amount=foreign_amount,
            foreign_currency_code=foreign_currency_code,
        )

        is_duplicate = result.get("duplicate", False)

        # Track in local state
        if not is_duplicate:
            _state.mark_api_transaction_processed(
                external_id=external_id,
                amount=amount,
                merchant=merchant,
                category=category,
                source=source,
            )

        logger.info(
            f"{'Duplicate' if is_duplicate else 'Created'}: "
            f"{merchant} {amount} {currency} [{category}] from {source}"
        )

        return jsonify({
            "status": "duplicate" if is_duplicate else "created",
            "category": category,
            "external_id": external_id,
        }), 200 if is_duplicate else 201

    except Exception as e:
        logger.error(f"Failed to create transaction: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/sms", methods=["POST"])
@require_api_key
def process_sms():
    """
    Accept raw SMS text from iPhone Shortcuts.
    Parses server-side using the full message_parser, then discards raw text.

    JSON body:
    {
        "text": "<raw SMS body>"
    }
    """
    data = request.get_json(force=True)
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "missing 'text' field"}), 400

    # Parse using the existing message parser
    parser = MessageParser()
    parsed = parser.parse(text, message_rowid=0, message_date=datetime.now())

    # Raw SMS text is NOT logged or stored — only parsed fields are used
    if parsed is None:
        logger.info("SMS received but not a transaction (OTP, promo, etc.)")
        return jsonify({"status": "skipped", "reason": "not a transaction"}), 200

    amount = parsed.amount
    currency = parsed.currency
    merchant = parsed.merchant_or_description
    txn_type = parsed.transaction_type.value
    date_str = datetime.now().isoformat()

    # Generate dedup ID
    external_id = _generate_external_id(date_str, amount, merchant)

    # Check if already processed
    if _state.is_api_transaction_processed(external_id):
        return jsonify({
            "status": "duplicate",
            "category": None,
            "external_id": external_id,
        }), 200

    # Categorize merchant
    category = _categorizer.categorize_single(merchant)
    _state.update_merchant_cache(_categorizer.cache)

    # Build Firefly III transaction
    asset_account = _config.get("firefly", {}).get(
        "asset_account_name", "Riyad Bank Account"
    )
    cc_account = _config.get("firefly", {}).get(
        "credit_card_account_name", "Riyad Bank Credit Card"
    )

    if txn_type == "transfer":
        source_name = asset_account
        destination_name = cc_account
    elif txn_type == "withdrawal":
        source_name = asset_account
        destination_name = merchant
    else:
        source_name = merchant
        destination_name = asset_account

    # Handle foreign currency
    firefly_amount = amount
    firefly_currency = "SAR"
    foreign_amount_val = None
    foreign_currency_code = None

    if currency != "SAR":
        foreign_amount_val = amount
        foreign_currency_code = currency
        firefly_amount = get_sar_amount(amount, currency)
        firefly_currency = "SAR"

    try:
        result = _firefly.create_transaction(
            transaction_type=txn_type,
            amount=firefly_amount,
            description=merchant,
            date=datetime.now(),
            source_name=source_name,
            destination_name=destination_name,
            category_name=category,
            currency_code=firefly_currency,
            notes="Auto-imported from iPhone SMS",
            external_id=external_id,
            tags=["auto-imported", "source:iphone"],
            foreign_amount=foreign_amount_val,
            foreign_currency_code=foreign_currency_code,
        )

        is_duplicate = result.get("duplicate", False)

        if not is_duplicate:
            _state.mark_api_transaction_processed(
                external_id=external_id,
                amount=amount,
                merchant=merchant,
                category=category,
                source="iphone",
            )

        logger.info(
            f"SMS {'Duplicate' if is_duplicate else 'Created'}: "
            f"{merchant} {amount} {currency} [{category}]"
        )

        return jsonify({
            "status": "duplicate" if is_duplicate else "created",
            "category": category,
            "merchant": merchant,
            "amount": amount,
            "currency": currency,
            "external_id": external_id,
        }), 200 if is_duplicate else 201

    except Exception as e:
        logger.error(f"Failed to create transaction from SMS: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
