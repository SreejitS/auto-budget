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
from shared.firefly_client import FireflyClient
from shared.state import StateManager

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

    if txn_type not in ("withdrawal", "deposit"):
        return jsonify({"error": "type must be 'withdrawal' or 'deposit'"}), 400

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

    if txn_type == "withdrawal":
        source_name = asset_account
        destination_name = merchant
    else:
        source_name = merchant
        destination_name = asset_account

    try:
        result = _firefly.create_transaction(
            transaction_type=txn_type,
            amount=amount,
            description=merchant,
            date=txn_date,
            source_name=source_name,
            destination_name=destination_name,
            category_name=category,
            currency_code=currency,
            notes=f"Auto-imported from {source}",
            external_id=external_id,
            tags=["auto-imported", f"source:{source}"],
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
