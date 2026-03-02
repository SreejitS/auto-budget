# Auto-Budget

Automatic expense tracking for Riyad Bank (Saudi Arabia). Reads transaction SMS
messages from macOS iMessage, parses them, categorizes spending, and pushes
everything into a self-hosted Firefly III instance for visualization and budgeting.

Runs unattended in the background via a Terminal.app Login Item.

## How It Works

```
iMessage (chat.db) --> Regex Parser --> Categorizer --> Firefly III
                           |               |               |
                      Arabic SMS      Rule-based +     Pie charts,
                      3000+ formats   Claude AI opt.   budget reports
```

1. Reads Riyad Bank SMS messages from the macOS iMessage database
2. Parses Arabic transaction messages using regex (99.2% accuracy on 3,765 messages)
3. Categorizes merchants into budget categories (Dining, Transport, Shopping, etc.)
4. Pushes transactions to Firefly III via REST API
5. Repeats every 15 minutes via Terminal Login Item

## Features

- Parses all Riyad Bank transaction types: online purchases, POS, international,
  ATM, transfers (Western Union, SARIE, local, internal), salary, bill payments,
  refunds, reversals, credit card payments
- Handles SAR, USD, EUR, INR, AED, TRY, and other currencies
- Hybrid categorization: rule-based (90% coverage, free) with optional Claude AI
  fallback for unknown merchants
- Privacy-first: all message parsing is done locally. Raw SMS text (card numbers,
  balances, OTPs) never leaves your machine. Only merchant names are sent to the
  Claude API for categorization, and only when rule-based matching fails.
- Three-layer deduplication: local state DB, Firefly external_id, and hash check
- Catches up on all missed messages after sleep/shutdown -- no data lost
- Filters out non-transaction messages: OTPs, promos, card status, login alerts

## Requirements

- macOS (tested on Ventura+)
- Python 3.9+
- Docker Desktop (for Firefly III)
- Full Disk Access granted to Terminal.app
- iPhone SMS forwarding enabled (Settings > Messages > Text Message Forwarding)
- Anthropic API key (optional -- the tool works without it using rule-based
  categorization; the AI improves coverage from 90% to ~99%)

## Quick Start

### 1. Clone and set up

```bash
cd /path/to/auto-budget
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy the example config files:

```bash
cp .env.example .env
cp config/config.yaml.example config/config.yaml
cp docker/.env.example docker/.env
cp docker/.db.env.example docker/.db.env
```

Edit `.env` to add your Anthropic API key (optional).

### 2. Grant Full Disk Access

System Settings > Privacy & Security > Full Disk Access > add **Terminal.app**.

This is required to read the iMessage database (`~/Library/Messages/chat.db`).

### 3. Enable iPhone SMS forwarding

On your iPhone: Settings > Messages > Text Message Forwarding > enable your Mac.

Bank messages are SMS (not iMessage), so they need this setting to appear on the Mac.

### 4. Start Firefly III

```bash
cd docker
docker compose up -d
```

Wait about 30 seconds for first-time initialization, then:

1. Open http://localhost:8080
2. Create an admin account
3. Go to Options > Profile > OAuth > Personal Access Tokens
4. Create a token and add it to `config/config.yaml`

### 5. Run your first sync

```bash
source venv/bin/activate
python3 -m src.sync
```

This reads all unprocessed messages, parses and categorizes them, and pushes
transactions to Firefly III.

### 6. Enable automatic background sync

Double-click `scripts/auto-budget-sync.command` to test it. If it runs
successfully, add it as a Login Item:

1. System Settings > General > Login Items & Extensions
2. Click **+** under "Open at Login"
3. Navigate to `scripts/auto-budget-sync.command` and select it

The sync runs every 15 minutes inside Terminal.app (which has Full Disk Access).
Minimize the Terminal window and it runs in the background.

To check logs:

```bash
tail -f logs/sync.log
```

## Why Terminal.app?

macOS TCC (Transparency, Consent, and Control) protects `~/Library/Messages/chat.db`
behind Full Disk Access. After testing multiple approaches (launchd agents,
compiled .app bundles, AppleScript apps, cron), the only reliable way to access
chat.db from a scheduled background process is through Terminal.app, which properly
propagates its FDA permission to child processes.

The Login Item approach runs a simple `while true; sleep 900` loop inside Terminal,
which syncs every 15 minutes. It starts automatically at login and just needs to
stay minimized.

## Configuration

### config/config.yaml

```yaml
imessage:
  sender_ids:
    - "RiyadBank"

firefly:
  base_url: "http://localhost:8080"
  api_token: "YOUR_TOKEN_HERE"
  asset_account_name: "Riyad Bank Account"

sync:
  batch_size: 100
```

### .env

```
ANTHROPIC_API_KEY=sk-ant-...   # Optional. Only needed for AI categorization.
```

## Working Without API Credits

The tool is fully functional without Anthropic API credits:

- Message parsing is 100% regex-based (no API needed)
- Rule-based categorization covers 90% of merchants (Dining, Transport,
  Shopping, Groceries, etc.)
- Remaining 10% are categorized as "Other"
- When you add API credits later, the AI categorizer automatically picks up
  where rules left off, and results are cached permanently

## Project Structure

```
auto-budget/
  src/
    imessage_reader.py      Read bank SMS from macOS iMessage database
    message_parser.py       Parse Arabic transaction messages (regex, local)
    categorizer.py          Rule-based + AI merchant categorization
    firefly_client.py       Firefly III REST API client
    sync.py                 Main sync orchestrator
    state.py                Local SQLite state management
  config/
    config.yaml.example     Configuration template
  docker/
    docker-compose.yml      Firefly III + MariaDB
  scripts/
    auto-budget-sync.command   Terminal Login Item (auto-sync every 15 min)
    sync_wrapper.sh            Sync wrapper script
    discover_format.py         Analyze iMessage SMS formats
    check_recent.py            Debug: check recent bank messages
  data/                     Local SQLite database (auto-created)
  logs/                     Sync logs (auto-created)
```

## Transaction Types Supported

| Type | Arabic | Direction |
|------|--------|-----------|
| Online Purchase | شراء إنترنت | Withdrawal |
| POS Purchase | شراء عبر نقاط بيع | Withdrawal |
| International Online | شراء إنترنت دولي | Withdrawal |
| International POS | شراء عبر نقاط بيع دولية | Withdrawal |
| ATM Withdrawal | سحب صراف | Withdrawal |
| Bill Payment | سداد فاتورة | Withdrawal |
| Outgoing Transfer | حوالة صادرة | Withdrawal |
| Western Union | حوالة ويسترن يونيون | Withdrawal |
| Credit Card Payment | بطاقة إئتمانية تسديد | Withdrawal |
| Salary | راتب | Deposit |
| Reversal/Refund | عملية عكسية | Deposit |
| Credit Card Refund | استرداد مبلغ | Deposit |
| Incoming Transfer | حوالة واردة | Deposit |
| Cash Back | استرجاع نقدي | Deposit |

## Budget Categories

Groceries, Dining, Transport, Shopping, Entertainment, Utilities, Healthcare,
Education, Subscriptions, Transfer, ATM, Salary, Other

## Viewing Your Budget

Once transactions are synced, open Firefly III at http://localhost:8080 to see:

- Dashboard with spending overview
- Pie charts by category
- Daily, weekly, monthly, and yearly reports
- Budget tracking and trends
- Individual transaction details with original SMS text in notes

## Troubleshooting

**"authorization denied" when reading iMessage**
Grant Full Disk Access to Terminal.app in System Settings > Privacy & Security.

**SMS not syncing from iPhone to Mac**
On iPhone: Settings > Messages > Text Message Forwarding > enable your Mac.
Toggle iMessage off/on if needed. Check iCloud Messages sync is enabled on both
devices.

**Firefly III not starting**
Check that Docker Desktop is running: `cd docker && docker compose logs`

**Messages not being parsed**
Run `python3 scripts/discover_format.py` to inspect your message formats.
The parser handles standard Riyad Bank formats. If the bank changes their
format, update patterns in `src/message_parser.py`.

**Sync not running automatically**
Make sure `scripts/auto-budget-sync.command` is added as a Login Item and
Terminal.app is running. Check `logs/sync.log` for errors.

## Privacy and Security

All message parsing happens locally on your machine. No raw SMS content is ever
sent to any external API. This means your card numbers, account balances, OTPs,
and transaction details stay private.

The only data sent to the Claude API is merchant names (e.g., "McDonald's",
"Amazon") for categorization -- and only when the local rule-based system cannot
categorize them. Results are cached permanently, so each merchant name is sent
at most once. Without an API key, everything runs fully offline.

## How Deduplication Works

Three layers prevent duplicate transactions:

1. Local state database tracks every processed iMessage ROWID
2. Each Firefly III transaction gets a unique external_id (`imsg_<ROWID>`)
3. Firefly III's built-in duplicate hash detection

This means you can safely re-run the sync at any time without creating duplicates.
