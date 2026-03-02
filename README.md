# Auto-Budget

Automatic expense tracking for Riyad Bank (Saudi Arabia). Reads bank SMS, parses
Arabic transaction messages, categorizes spending, and pushes to a self-hosted
Firefly III instance.

## Architecture

Two deployment modes:

```
iPhone (primary, real-time)              RPi Server (Tailscale)
─────────────────────────                ─────────────────────────
Bank SMS arrives                         Flask API (:5000)
  → iOS Shortcuts Automation               → Categorize merchant
    → Extract: amount, merchant,           → Push to Firefly III (:8080)
      currency, type                       → Return category + status
    → POST safe fields to RPi
                                         Firefly III Dashboard
  Never sent: card numbers,                → Accessible from anywhere
  OTPs, balances, raw SMS                    via Tailscale


Mac (backup, every 15 min)
────────────────────────────
iMessage syncs from iPhone
  → Terminal Login Item reads chat.db
    → Python regex parser (3000+ formats)
      → Same categorizer + Firefly push
```

## Project Structure

```
auto-budget/
├── server/                 RPi server (Flask API + Docker)
│   ├── src/api.py          REST API endpoint
│   ├── docker/             Docker Compose (Firefly III + MariaDB + API)
│   ├── scripts/            RPi setup script
│   ├── Dockerfile
│   └── README.md           Server setup guide
│
├── mac/                    macOS client (iMessage reader)
│   ├── src/                imessage_reader, parser, sync orchestrator
│   └── scripts/            Terminal Login Item, sync wrapper
│
├── shared/                 Code used by both
│   ├── categorizer.py      Rule-based + Claude AI categorization
│   ├── firefly_client.py   Firefly III REST API client
│   └── state.py            SQLite state management
│
└── config/                 Shared configuration
    ├── config.yaml.example
    └── categories.yaml
```

## Quick Start

### Option A: RPi Server (recommended)

See [server/README.md](server/README.md) for full setup guide.

1. Flash RPi OS Lite, install Docker + Tailscale
2. Clone this repo, run `server/scripts/setup-rpi.sh`
3. Set up Firefly III, get API token
4. Create iPhone Shortcut automation
5. Done — transactions appear in Firefly III in real-time

### Option B: Mac only

1. `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
2. Grant Full Disk Access to Terminal.app
3. Start Firefly III: `cd docker && docker compose up -d`
4. Run: `PYTHONPATH=. python3 -m mac.src.sync`
5. Add `mac/scripts/auto-budget-sync.command` as Login Item for auto-sync

## Features

- Parses all Riyad Bank SMS types (purchases, ATM, transfers, salary, refunds)
- Handles SAR, USD, EUR, INR, AED, TRY currencies
- Rule-based categorization (90% coverage) + optional Claude AI fallback
- Privacy-first: raw SMS never leaves your device. Only merchant names sent to AI.
- Three-layer deduplication prevents duplicates
- Works fully offline without API credits
