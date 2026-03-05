"""
Firefly III REST API client.

Handles creating transactions, accounts, and categories via the
Firefly III API. Uses Personal Access Token authentication.
"""

import logging
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger("auto-budget")


class FireflyClient:
    """REST API client for Firefly III."""

    def __init__(self, base_url: str, token: str):
        """
        Args:
            base_url: Firefly III URL, e.g. "http://localhost:8080"
            token: Personal Access Token from Firefly III profile.
        """
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api/v1"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/vnd.api+json",
            }
        )

    # --- Accounts ---

    def get_accounts(self, account_type: str = "asset") -> list[dict]:
        """Get all accounts of a given type.

        Args:
            account_type: "asset", "expense", "revenue", "cash", "liability"
        """
        resp = self.session.get(
            f"{self.api_url}/accounts", params={"type": account_type}
        )
        resp.raise_for_status()
        return resp.json()["data"]

    def create_account(
        self,
        name: str,
        account_type: str,
        currency_code: str = "SAR",
        account_role: Optional[str] = None,
    ) -> dict:
        """Create a new account.

        Args:
            name: Account name.
            account_type: "asset", "expense", "revenue".
            currency_code: Currency code (default SAR).
            account_role: Role for asset accounts ("defaultAsset", "cashWalletAsset").
        """
        payload = {
            "name": name,
            "type": account_type,
            "currency_code": currency_code,
        }
        if account_role:
            payload["account_role"] = account_role
        resp = self.session.post(f"{self.api_url}/accounts", json=payload)
        resp.raise_for_status()
        return resp.json()["data"]

    def find_or_create_account(
        self, name: str, account_type: str, currency_code: str = "SAR"
    ) -> dict:
        """Find an account by name, or create it if it doesn't exist."""
        accounts = self.get_accounts(account_type)
        for acct in accounts:
            if acct["attributes"]["name"].lower() == name.lower():
                return acct
        return self.create_account(name, account_type, currency_code)

    # --- Categories ---

    def get_categories(self) -> list[dict]:
        """Get all categories."""
        resp = self.session.get(f"{self.api_url}/categories")
        resp.raise_for_status()
        return resp.json()["data"]

    def create_category(self, name: str) -> dict:
        """Create a new category."""
        resp = self.session.post(
            f"{self.api_url}/categories", json={"name": name}
        )
        resp.raise_for_status()
        return resp.json()["data"]

    def find_or_create_category(self, name: str) -> dict:
        """Find a category by name, or create it if it doesn't exist."""
        categories = self.get_categories()
        for cat in categories:
            if cat["attributes"]["name"].lower() == name.lower():
                return cat
        return self.create_category(name)

    # --- Transactions ---

    def create_transaction(
        self,
        transaction_type: str,
        amount: float,
        description: str,
        date: datetime,
        source_name: str,
        destination_name: str,
        category_name: Optional[str] = None,
        currency_code: str = "SAR",
        notes: Optional[str] = None,
        external_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        foreign_amount: Optional[float] = None,
        foreign_currency_code: Optional[str] = None,
    ) -> dict:
        """Create a transaction in Firefly III.

        Args:
            transaction_type: "withdrawal", "deposit", or "transfer".
            amount: Transaction amount (positive).
            description: Human-readable description.
            date: Transaction date.
            source_name: Source account name.
                For withdrawals: your bank account (asset).
                For deposits: the payer (revenue account, auto-created).
            destination_name: Destination account name.
                For withdrawals: the merchant (expense account, auto-created).
                For deposits: your bank account (asset).
            category_name: Budget category name.
            currency_code: Currency code (default SAR).
            notes: Additional notes (e.g., original SMS text).
            external_id: Unique ID for deduplication (e.g., "imsg_12345").
            tags: List of tags (e.g., ["auto-imported"]).
            foreign_amount: Original amount in foreign currency.
            foreign_currency_code: 3-letter code of the foreign currency.

        Returns:
            Transaction data dict, or dict with "duplicate": True if already exists.
        """
        transaction_split = {
            "type": transaction_type,
            "date": date.strftime("%Y-%m-%dT%H:%M:%S+03:00"),  # Riyadh UTC+3
            "amount": str(round(amount, 2)),
            "description": description,
            "currency_code": currency_code,
            "source_name": source_name,
            "destination_name": destination_name,
        }

        if category_name:
            transaction_split["category_name"] = category_name
        if notes:
            transaction_split["notes"] = notes
        if external_id:
            transaction_split["external_id"] = str(external_id)
        if tags:
            transaction_split["tags"] = tags
        if foreign_amount is not None and foreign_currency_code:
            transaction_split["foreign_amount"] = str(round(foreign_amount, 2))
            transaction_split["foreign_currency_code"] = foreign_currency_code

        payload = {
            "error_if_duplicate_hash": True,
            "apply_rules": True,
            "transactions": [transaction_split],
        }

        resp = self.session.post(
            f"{self.api_url}/transactions", json=payload
        )

        # 422 with duplicate hash means already exists - not an error
        if resp.status_code == 422:
            error_data = resp.json()
            if "duplicate" in str(error_data).lower():
                logger.debug(
                    f"Duplicate transaction skipped: {description}"
                )
                return {"duplicate": True}
            logger.error(
                f"Firefly API 422 for '{description}': "
                f"{error_data.get('message', error_data)}"
            )
            resp.raise_for_status()

        resp.raise_for_status()
        return resp.json()["data"]

    def test_connection(self) -> bool:
        """Test the API connection and authentication."""
        try:
            resp = self.session.get(f"{self.api_url}/about")
            resp.raise_for_status()
            data = resp.json()["data"]
            logger.info(
                f"Connected to Firefly III v{data.get('version', 'unknown')}"
            )
            return True
        except Exception as e:
            logger.error(f"Firefly III connection failed: {e}")
            return False
