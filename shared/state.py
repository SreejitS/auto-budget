"""
State management for the auto-budget sync process.

Uses a local SQLite database to track:
- Last processed iMessage ROWID
- Merchant -> category cache (reduces Claude API calls)
- Processed transactions (for deduplication)
- Sync history log
"""

import os
import sqlite3
from datetime import datetime


class StateManager:
    """Manages sync state in a local SQLite database."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS merchant_cache (
                    merchant TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_transactions (
                    message_rowid INTEGER PRIMARY KEY,
                    firefly_transaction_id INTEGER,
                    amount REAL,
                    merchant TEXT,
                    category TEXT,
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    messages_found INTEGER DEFAULT 0,
                    transactions_parsed INTEGER DEFAULT 0,
                    transactions_pushed INTEGER DEFAULT 0,
                    errors TEXT,
                    status TEXT DEFAULT 'running'
                );

                CREATE TABLE IF NOT EXISTS api_transactions (
                    external_id TEXT PRIMARY KEY,
                    amount REAL,
                    merchant TEXT,
                    category TEXT,
                    source TEXT,
                    created_at TEXT NOT NULL
                );
            """
            )

    # --- Last Processed ROWID ---

    def get_last_processed_rowid(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT value FROM sync_state WHERE key = 'last_processed_rowid'"
            ).fetchone()
            return int(row[0]) if row else 0

    def set_last_processed_rowid(self, rowid: int):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sync_state (key, value, updated_at)
                   VALUES ('last_processed_rowid', ?, ?)""",
                (str(rowid), now),
            )

    # --- Merchant Cache ---

    def get_merchant_cache(self) -> dict[str, str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT merchant, category FROM merchant_cache"
            ).fetchall()
            return {r[0]: r[1] for r in rows}

    def update_merchant_cache(self, cache: dict[str, str]):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            for merchant, category in cache.items():
                conn.execute(
                    """INSERT OR REPLACE INTO merchant_cache
                       (merchant, category, created_at)
                       VALUES (?, ?, ?)""",
                    (merchant, category, now),
                )

    # --- Processed Transactions ---

    def is_transaction_processed(self, message_rowid: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_transactions WHERE message_rowid = ?",
                (message_rowid,),
            ).fetchone()
            return row is not None

    def mark_transaction_processed(
        self,
        message_rowid: int,
        firefly_id: int,
        amount: float,
        merchant: str,
        category: str,
    ):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO processed_transactions
                   (message_rowid, firefly_transaction_id, amount,
                    merchant, category, processed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (message_rowid, firefly_id, amount, merchant, category, now),
            )

    # --- API Transactions (RPi dedup) ---

    def is_api_transaction_processed(self, external_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM api_transactions WHERE external_id = ?",
                (external_id,),
            ).fetchone()
            return row is not None

    def mark_api_transaction_processed(
        self,
        external_id: str,
        amount: float,
        merchant: str,
        category: str,
        source: str,
    ):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO api_transactions
                   (external_id, amount, merchant, category, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (external_id, amount, merchant, category, source, now),
            )

    # --- AI Parse Cache ---
    # Caches Claude's parse results by message format signature.
    # When the bank changes SMS format, Claude parses it once,
    # and the result is reused for all similar messages.

    def get_ai_parse_cache(self) -> dict[str, str]:
        """Get all cached AI parse results. Key=signature, value=JSON result."""
        with sqlite3.connect(self.db_path) as conn:
            # Create table if not exists (migration-safe)
            conn.execute(
                """CREATE TABLE IF NOT EXISTS ai_parse_cache (
                    signature TEXT PRIMARY KEY,
                    parse_result TEXT NOT NULL,
                    sample_text TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
            rows = conn.execute(
                "SELECT signature, parse_result FROM ai_parse_cache"
            ).fetchall()
            return {r[0]: r[1] for r in rows}

    def save_ai_parse_result(
        self, signature: str, parse_result: str, sample_text: str
    ):
        """Cache an AI parse result by format signature."""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS ai_parse_cache (
                    signature TEXT PRIMARY KEY,
                    parse_result TEXT NOT NULL,
                    sample_text TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """INSERT OR REPLACE INTO ai_parse_cache
                   (signature, parse_result, sample_text, created_at)
                   VALUES (?, ?, ?, ?)""",
                (signature, parse_result, sample_text, now),
            )

    # --- Sync Log ---

    def start_sync_log(self) -> int:
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO sync_log (started_at) VALUES (?)", (now,)
            )
            return cursor.lastrowid

    def complete_sync_log(
        self,
        log_id: int,
        messages_found: int,
        transactions_parsed: int,
        transactions_pushed: int,
        errors: str = None,
        status: str = "completed",
    ):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE sync_log
                   SET completed_at=?, messages_found=?,
                       transactions_parsed=?, transactions_pushed=?,
                       errors=?, status=?
                   WHERE id=?""",
                (
                    now,
                    messages_found,
                    transactions_parsed,
                    transactions_pushed,
                    errors,
                    status,
                    log_id,
                ),
            )
