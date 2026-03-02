"""
iMessage database reader for extracting bank transaction messages.

Reads from ~/Library/Messages/chat.db in read-only mode.
Requires Full Disk Access permission for the running process.
"""

import os
import re
import sqlite3
import datetime
from dataclasses import dataclass
from typing import Optional


# Apple Cocoa epoch: January 1, 2001
APPLE_EPOCH = datetime.datetime(2001, 1, 1)


def apple_timestamp_to_datetime(timestamp: int) -> datetime.datetime:
    """Convert Apple Cocoa timestamp to Python datetime.

    Modern macOS (Ventura+) stores timestamps in nanoseconds since 2001-01-01.
    Older macOS used seconds. We detect which format based on magnitude.
    """
    if timestamp is None or timestamp == 0:
        return datetime.datetime.now()
    if timestamp > 1_000_000_000_000:  # nanoseconds
        seconds = timestamp / 1_000_000_000
    else:
        seconds = timestamp
    return APPLE_EPOCH + datetime.timedelta(seconds=seconds)


def extract_text_from_attributed_body(blob: bytes) -> Optional[str]:
    """Extract plain text from NSAttributedString blob.

    On modern macOS, some messages store text in the attributedBody column
    as a serialized NSAttributedString rather than the text column.
    """
    if blob is None:
        return None
    try:
        # The blob contains a streamtyped NSAttributedString.
        # The plain text is embedded between specific byte sequences.
        # We try multiple extraction strategies.

        decoded = blob.decode("utf-8", errors="ignore")

        # Strategy 1: Look for text after "NSString" marker
        match = re.search(
            r"NSString.{1,40}?([^\x00\x01\x02\x03\x04\x05]{5,})", decoded
        )
        if match:
            text = match.group(1).strip()
            if len(text) > 3:
                return text

        # Strategy 2: Extract longest readable ASCII sequence
        text_parts = re.findall(r"[\x20-\x7E\n]{10,}", decoded)
        if text_parts:
            return max(text_parts, key=len).strip()

        return None
    except Exception:
        return None


@dataclass
class RawMessage:
    """A raw message read from the iMessage database."""

    rowid: int
    text: str
    date: datetime.datetime
    handle_id: int
    sender_id: str


class IMessageReader:
    """Reads messages from the macOS iMessage SQLite database."""

    CHAT_DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")

    def __init__(self, sender_ids: list[str]):
        """
        Args:
            sender_ids: List of possible sender identifiers for the bank
                       (e.g., ["RiyadBank", "RIYAD", "+966XXXXXXXXX"]).
                       Use discover_bank_senders() to find these.
        """
        self.sender_ids = sender_ids
        # When launched via the AutoBudgetSync.app (which has FDA),
        # the C launcher copies chat.db to a temp path and sets this env var.
        copy_path = os.environ.get("IMESSAGE_DB_COPY")
        if copy_path and os.path.exists(copy_path):
            self.db_path = copy_path
        else:
            self.db_path = self.CHAT_DB_PATH

    def _connect(self) -> sqlite3.Connection:
        """Connect to chat.db in read-only mode."""
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def discover_bank_senders(self, search_terms: Optional[list[str]] = None) -> list[dict]:
        """Find potential bank sender handles in the iMessage database.

        Searches for handles containing common bank-related terms.

        Args:
            search_terms: Additional search terms beyond defaults.

        Returns:
            List of dicts with keys: rowid, id, service
        """
        terms = ["riyad", "riyadbank", "bank"]
        if search_terms:
            terms.extend(search_terms)

        conn = self._connect()
        try:
            # Build WHERE clause for all search terms
            conditions = " OR ".join(
                "LOWER(h.id) LIKE ?" for _ in terms
            )
            params = [f"%{t.lower()}%" for t in terms]

            query = f"""
                SELECT DISTINCT h.ROWID as rowid, h.id, h.service
                FROM handle h
                WHERE {conditions}
                ORDER BY h.ROWID
            """
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_all_senders(self) -> list[dict]:
        """Get all unique sender handles (useful for manual inspection).

        Returns:
            List of dicts with keys: rowid, id, service, message_count
        """
        conn = self._connect()
        try:
            query = """
                SELECT h.ROWID as rowid, h.id, h.service,
                       COUNT(m.ROWID) as message_count
                FROM handle h
                LEFT JOIN message m ON m.handle_id = h.ROWID AND m.is_from_me = 0
                GROUP BY h.ROWID, h.id, h.service
                HAVING message_count > 0
                ORDER BY message_count DESC
            """
            rows = conn.execute(query).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_messages(
        self, since_rowid: int = 0, limit: int = 1000
    ) -> list[RawMessage]:
        """Fetch messages from known bank senders newer than since_rowid.

        Args:
            since_rowid: Only fetch messages with ROWID > this value.
            limit: Maximum number of messages to return.

        Returns:
            List of RawMessage objects, ordered by ROWID ascending.
        """
        if not self.sender_ids:
            return []

        conn = self._connect()
        try:
            placeholders = ",".join("?" for _ in self.sender_ids)
            query = f"""
                SELECT
                    m.ROWID as rowid,
                    m.text,
                    m.date,
                    m.attributedBody,
                    m.handle_id,
                    h.id as sender_id
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE h.id IN ({placeholders})
                  AND m.is_from_me = 0
                  AND m.ROWID > ?
                ORDER BY m.ROWID ASC
                LIMIT ?
            """
            params = list(self.sender_ids) + [since_rowid, limit]
            rows = conn.execute(query, params).fetchall()

            messages = []
            for row in rows:
                text = row["text"]
                if text is None:
                    text = extract_text_from_attributed_body(row["attributedBody"])
                if text is None:
                    continue  # Skip messages with no extractable text

                messages.append(
                    RawMessage(
                        rowid=row["rowid"],
                        text=text.strip(),
                        date=apple_timestamp_to_datetime(row["date"]),
                        handle_id=row["handle_id"],
                        sender_id=row["sender_id"],
                    )
                )
            return messages
        finally:
            conn.close()

    def get_all_bank_messages(self) -> list[RawMessage]:
        """Fetch ALL messages from known bank senders (for format discovery)."""
        return self.get_messages(since_rowid=0, limit=10000)

    def search_messages_by_content(
        self, keywords: list[str], limit: int = 100
    ) -> list[RawMessage]:
        """Search all messages for content containing any of the keywords.

        Useful for finding bank messages when the sender handle is unknown.
        """
        conn = self._connect()
        try:
            conditions = " OR ".join("m.text LIKE ?" for _ in keywords)
            params = [f"%{kw}%" for kw in keywords]

            query = f"""
                SELECT
                    m.ROWID as rowid,
                    m.text,
                    m.date,
                    m.handle_id,
                    h.id as sender_id
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.is_from_me = 0
                  AND m.text IS NOT NULL
                  AND ({conditions})
                ORDER BY m.ROWID DESC
                LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(query, params).fetchall()

            return [
                RawMessage(
                    rowid=row["rowid"],
                    text=row["text"].strip(),
                    date=apple_timestamp_to_datetime(row["date"]),
                    handle_id=row["handle_id"],
                    sender_id=row["sender_id"],
                )
                for row in rows
                if row["text"]
            ]
        finally:
            conn.close()
