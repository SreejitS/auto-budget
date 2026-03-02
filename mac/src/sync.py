"""
Main sync orchestrator for auto-budget.

Ties together: iMessage reading -> parsing -> categorization -> Firefly III push.
Designed to run both manually and as a scheduled background job (launchd).
"""

import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Add project root to path for shared imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.categorizer import TransactionCategorizer
from shared.firefly_client import FireflyClient
from shared.state import StateManager
from .imessage_reader import IMessageReader
from .message_parser import MessageParser, TransactionType
MAC_DIR = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
STATE_DB_PATH = MAC_DIR / "data" / "auto-budget.db"
LOG_PATH = MAC_DIR / "logs" / "sync.log"


def setup_logging() -> logging.Logger:
    os.makedirs(LOG_PATH.parent, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("auto-budget")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


class BudgetSync:
    """Orchestrates the full sync pipeline."""

    def __init__(self):
        # Load environment and config
        load_dotenv(PROJECT_ROOT / ".env")
        self.logger = setup_logging()
        self.config = load_config()
        self.state = StateManager(str(STATE_DB_PATH))

        # Initialize components
        self.reader = IMessageReader(
            sender_ids=self.config["imessage"]["sender_ids"]
        )

        self.parser = MessageParser()

        self.categorizer = TransactionCategorizer(
            cache=self.state.get_merchant_cache()
        )

        self.firefly = FireflyClient(
            base_url=self.config["firefly"]["base_url"],
            token=self.config["firefly"]["api_token"],
        )

        self.asset_account_name = self.config["firefly"]["asset_account_name"]
        self.batch_size = self.config.get("sync", {}).get("batch_size", 100)

    def run(self):
        """Execute one sync cycle."""
        log_id = self.state.start_sync_log()
        self.logger.info("Starting sync cycle")

        try:
            # Step 1: Test Firefly III connection
            if not self.firefly.test_connection():
                raise ConnectionError(
                    "Cannot connect to Firefly III. Is it running?"
                )

            # Ensure the asset account exists
            self.firefly.find_or_create_account(
                self.asset_account_name, "asset", "SAR"
            )

            # Step 2: Read new messages
            last_rowid = self.state.get_last_processed_rowid()
            self.logger.info(f"Reading messages after ROWID {last_rowid}")

            messages = self.reader.get_messages(
                since_rowid=last_rowid, limit=self.batch_size
            )
            self.logger.info(f"Found {len(messages)} new message(s)")

            if not messages:
                self.state.complete_sync_log(
                    log_id, 0, 0, 0, status="completed"
                )
                self.logger.info("No new messages. Sync complete.")
                return

            # Step 3: Parse transactions from messages (regex only - no
            # raw SMS text is ever sent to external APIs for security)
            transactions = []
            for msg in messages:
                parsed = self.parser.parse(
                    text=msg.text,
                    message_rowid=msg.rowid,
                    message_date=msg.date,
                )
                if parsed:
                    transactions.append(parsed)
                else:
                    self.logger.debug(
                        f"Skipped non-transaction ROWID={msg.rowid}"
                    )

            self.logger.info(
                f"Parsed {len(transactions)} transaction(s) "
                f"from {len(messages)} message(s)"
            )

            # Step 4: Categorize transactions
            # Only merchant names are sent to Claude API (never raw SMS
            # text, card numbers, balances, or other sensitive data)
            categories = []
            if transactions:
                merchant_data = [
                    {"merchant": t.merchant_or_description}
                    for t in transactions
                ]
                categories = self.categorizer.categorize_batch(merchant_data)

                # Persist updated merchant cache
                self.state.update_merchant_cache(self.categorizer.cache)

            # Step 5: Push transactions to Firefly III
            pushed_count = 0
            for txn, category in zip(transactions, categories):
                # Skip already processed
                if self.state.is_transaction_processed(txn.message_rowid):
                    self.logger.debug(
                        f"Skipping already processed ROWID={txn.message_rowid}"
                    )
                    continue

                try:
                    result = self._push_to_firefly(txn, category)

                    firefly_id = 0
                    if result and not result.get("duplicate"):
                        firefly_id = result.get("id", 0)

                    self.state.mark_transaction_processed(
                        message_rowid=txn.message_rowid,
                        firefly_id=firefly_id,
                        amount=txn.amount,
                        merchant=txn.merchant_or_description,
                        category=category,
                    )
                    pushed_count += 1
                    self.logger.info(
                        f"Pushed: {txn.transaction_type.value} "
                        f"SAR {txn.amount:.2f} "
                        f"at {txn.merchant_or_description} [{category}]"
                    )
                except Exception as e:
                    self.logger.error(
                        f"Failed to push ROWID={txn.message_rowid}: {e}"
                    )

            # Step 6: Update last processed ROWID
            max_rowid = max(m.rowid for m in messages)
            self.state.set_last_processed_rowid(max_rowid)

            self.state.complete_sync_log(
                log_id, len(messages), len(transactions), pushed_count
            )
            self.logger.info(
                f"Sync complete: {len(messages)} messages, "
                f"{len(transactions)} transactions, {pushed_count} pushed"
            )

        except Exception as e:
            self.logger.error(f"Sync failed: {e}", exc_info=True)
            self.state.complete_sync_log(
                log_id, 0, 0, 0, errors=str(e), status="failed"
            )
            raise

    def _push_to_firefly(self, txn, category: str) -> dict:
        """Map a parsed transaction to a Firefly III API call."""
        if txn.transaction_type == TransactionType.WITHDRAWAL:
            firefly_type = "withdrawal"
            source_name = self.asset_account_name
            destination_name = txn.merchant_or_description or "Unknown Merchant"
        else:
            firefly_type = "deposit"
            source_name = txn.merchant_or_description or "Unknown Source"
            destination_name = self.asset_account_name

        description = txn.merchant_or_description
        if txn.card_last_4:
            description += f" (card ***{txn.card_last_4})"

        return self.firefly.create_transaction(
            transaction_type=firefly_type,
            amount=txn.amount,
            description=description,
            date=txn.date,
            source_name=source_name,
            destination_name=destination_name,
            category_name=category,
            currency_code="SAR",
            notes=f"Auto-imported from iMessage.\nOriginal: {txn.raw_text}",
            external_id=f"imsg_{txn.message_rowid}",
            tags=["auto-imported"],
        )


def main():
    syncer = BudgetSync()
    syncer.run()


if __name__ == "__main__":
    main()
