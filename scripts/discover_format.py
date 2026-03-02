#!/usr/bin/env python3
"""
One-time discovery script to find Riyad Bank sender handles and analyze
the SMS message format from your iMessage history.

Usage:
    python3 scripts/discover_format.py

Prerequisites:
    - Full Disk Access granted to your terminal app
    - ANTHROPIC_API_KEY set in environment or .env file
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from src.imessage_reader import IMessageReader


def discover_senders(reader: IMessageReader) -> list[dict]:
    """Step 1: Find Riyad Bank sender handles."""
    print("=" * 60)
    print("STEP 1: Discovering bank sender handles")
    print("=" * 60)

    # Search by handle name
    candidates = reader.discover_bank_senders()

    if candidates:
        print(f"\nFound {len(candidates)} potential bank handle(s):\n")
        for c in candidates:
            print(f"  [{c['rowid']}] id='{c['id']}' service='{c['service']}'")
    else:
        print("\nNo handles matching 'riyad' or 'bank' found.")

    # Also search by message content as fallback
    print("\n\nSearching messages containing bank-related keywords...")
    content_matches = reader.search_messages_by_content(
        keywords=["SAR", "debit", "credit", "purchase", "withdrawal", "balance"],
        limit=50,
    )

    if content_matches:
        # Find unique senders from content matches
        sender_ids = set()
        for msg in content_matches:
            sender_ids.add(msg.sender_id)

        print(f"Found messages from {len(sender_ids)} sender(s) with bank keywords:\n")
        for sid in sorted(sender_ids):
            count = sum(1 for m in content_matches if m.sender_id == sid)
            sample = next(m for m in content_matches if m.sender_id == sid)
            print(f"  Sender: '{sid}' ({count} matching messages)")
            print(f"  Sample: {sample.text[:120]}...")
            print()
    else:
        print("No messages with bank keywords found.")

    # Combine all candidate sender IDs
    all_candidates = []
    seen = set()
    for c in candidates:
        if c["id"] not in seen:
            all_candidates.append(c)
            seen.add(c["id"])
    if content_matches:
        for sid in sender_ids:
            if sid not in seen:
                all_candidates.append({"rowid": -1, "id": sid, "service": "unknown"})
                seen.add(sid)

    return all_candidates


def show_messages(reader: IMessageReader, sender_id: str) -> list[str]:
    """Step 2: Show all messages from the selected sender."""
    print("\n" + "=" * 60)
    print(f"STEP 2: Messages from '{sender_id}'")
    print("=" * 60)

    reader.sender_ids = [sender_id]
    messages = reader.get_all_bank_messages()

    print(f"\nFound {len(messages)} total messages.\n")

    texts = []
    for i, msg in enumerate(messages):
        print(f"--- Message {i + 1} | ROWID={msg.rowid} | {msg.date} ---")
        print(msg.text)
        print()
        texts.append(msg.text)

    return texts


def analyze_with_claude(texts: list[str]):
    """Step 3: Use Claude to analyze message formats and generate regex patterns."""
    print("\n" + "=" * 60)
    print("STEP 3: AI Format Analysis")
    print("=" * 60)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "\nSkipping AI analysis: ANTHROPIC_API_KEY not set."
            "\nSet it in .env or environment to enable automatic pattern generation."
        )
        return

    try:
        from anthropic import Anthropic
    except ImportError:
        print("\nSkipping AI analysis: 'anthropic' package not installed.")
        print("Run: pip install anthropic")
        return

    client = Anthropic()

    # Take a representative sample (up to 30 messages)
    sample_texts = texts[:30]

    print(f"\nAnalyzing {len(sample_texts)} sample messages with Claude...\n")

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": f"""Analyze these bank SMS messages from Riyad Bank (Saudi Arabia).
Identify ALL distinct message format patterns you can find.

For each pattern, provide:
1. Pattern name (e.g., "Purchase Debit", "ATM Withdrawal", "Salary Credit", "Transfer", "Refund")
2. A Python regex pattern with named groups that captures:
   - amount: the transaction amount
   - merchant: merchant name or description (if present)
   - card: last 4 digits of card (if present)
   - balance: available balance (if present)
3. The transaction_type: "withdrawal" or "deposit"
4. An example message that matches

Also identify any messages that are NOT financial transactions (OTP codes, promotional, etc.)
so we can filter them out.

Messages:
{chr(10).join(f'MSG {i+1}: {t}' for i, t in enumerate(sample_texts))}

Output the patterns as a Python list of dicts ready to paste into config, like:
```python
patterns = [
    {{
        "name": "purchase",
        "regex": r"...",
        "transaction_type": "withdrawal"
    }},
    ...
]
```

Also output a list of non-transaction message patterns to skip.""",
            }
        ],
    )
    print(response.content[0].text)


def main():
    reader = IMessageReader(sender_ids=[])

    # Step 1: Discover senders
    candidates = discover_senders(reader)

    if not candidates:
        print("\nNo bank sender handles found.")
        print("Make sure you have Riyad Bank SMS alerts enabled and")
        print("your iMessage/SMS history contains bank messages.")

        # Show all senders for manual inspection
        print("\n\nAll message senders in your iMessage database:")
        all_senders = reader.get_all_senders()
        for s in all_senders[:50]:
            print(f"  '{s['id']}' ({s['message_count']} messages, {s['service']})")
        return

    # Let user select
    if len(candidates) == 1:
        selected = candidates[0]["id"]
        print(f"\nAuto-selected sender: '{selected}'")
    else:
        print("\nMultiple candidates found. Enter the sender ID to analyze:")
        for i, c in enumerate(candidates):
            print(f"  {i + 1}. {c['id']}")
        choice = input("\nEnter number or sender ID: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            selected = candidates[int(choice) - 1]["id"]
        else:
            selected = choice

    # Step 2: Show messages
    texts = show_messages(reader, selected)

    if not texts:
        print("No messages found from this sender.")
        return

    # Step 3: AI analysis
    analyze = input("\nRun AI format analysis? (y/n): ").strip().lower()
    if analyze in ("y", "yes", ""):
        analyze_with_claude(texts)

    print("\n" + "=" * 60)
    print("NEXT STEPS:")
    print("=" * 60)
    print(f"1. Add '{selected}' to config/config.yaml under imessage.sender_ids")
    print("2. If AI generated regex patterns, add them to config/config.yaml")
    print("3. Set up Firefly III: cd docker && docker compose up -d")
    print("4. Run first sync: python3 -m src.sync")


if __name__ == "__main__":
    main()
