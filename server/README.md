# Auto-Budget Server (Raspberry Pi)

REST API that receives pre-parsed bank transactions from iPhone Shortcuts,
categorizes merchants, and pushes to Firefly III.

## Architecture

```
iPhone                              RPi (this server)
──────                              ─────────────────
Bank SMS → Shortcuts                Flask API (:5000)
  → Extract safe fields               POST /api/transaction
  → POST to RPi API                     → Categorize (rules + AI)
                                        → Push to Firefly III (:8080)
Only sent: amount, merchant,
currency, type, date                Firefly III Dashboard (:8080)
                                      → Budget reports + charts
Never sent: card numbers,
OTPs, balances, raw SMS             All via Tailscale VPN
```

## API Endpoints

### POST /api/transaction

Create a transaction in Firefly III.

```bash
curl -X POST http://<tailscale-ip>:5000/api/transaction \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 48.00,
    "currency": "SAR",
    "merchant": "McDonald'\''s",
    "type": "withdrawal",
    "date": "2026-03-02T14:30:00",
    "source": "iphone"
  }'
```

Response:
```json
{ "status": "created", "category": "Dining", "external_id": "txn_abc123def456" }
```

### GET /health

```bash
curl http://<tailscale-ip>:5000/health
```

Response:
```json
{ "status": "ok", "firefly_connected": true, "merchant_cache_size": 52 }
```

## Setup

### Prerequisites

- Raspberry Pi 4 (4GB+ RAM)
- microSD card (16GB+)
- Tailscale account

### 1. Flash Raspberry Pi OS

Use Raspberry Pi Imager:
- Choose **Raspberry Pi OS Lite (64-bit)**
- Enable SSH, set hostname, configure Wi-Fi
- Set timezone to Asia/Riyadh

### 2. Run setup script

```bash
ssh user@auto-budget.local
git clone <repo-url> ~/auto-budget
cd ~/auto-budget
bash server/scripts/setup-rpi.sh
```

### 3. Set up Tailscale

```bash
sudo tailscale up
# Note your Tailscale IP (100.x.y.z)
```

### 4. Configure Firefly III

1. Open `http://<tailscale-ip>:8080`
2. Create admin account
3. Profile > OAuth > Personal Access Tokens > Create
4. Add token to `server/.env` and `config/config.yaml`
5. Restart: `cd server/docker && docker compose restart auto-budget-api`

### 5. Verify

```bash
curl http://localhost:5000/health
```

## iPhone Shortcut Setup

### Automation (auto-trigger on SMS)

1. Shortcuts > Automation > + > Message
2. Sender: RiyadBank, Contains: مبلغ
3. Run Immediately (iOS 18+)
4. Run Shortcut: "Auto Budget"

### Shortcut Actions

1. **Match Text** — amount: `مبلغ[^\n]*?([\d,]+\.?\d*)`
2. **Match Text** — currency: `(SAR|USD|EUR|INR|AED|TRY|ر\.س)`
3. **Match Text** — merchant: `(?:لدى|من|إلى|جهة)[:\s]+(.+)`
4. **If** contains شراء/سحب/سداد/خصم → type = "withdrawal", else "deposit"
5. **Get Contents of URL** — POST to `http://<tailscale-ip>:5000/api/transaction`
   - Headers: `X-API-Key: <your-key>`
   - Body: JSON with amount, currency, merchant, type, date

### Manual Fallback (Share Sheet)

Enable "Show in Share Sheet" in Shortcut settings. Then in Messages:
long-press any bank SMS > Share > "Auto Budget"

## Security

- **Tailscale**: API only reachable from your Tailscale network
- **API Key**: Required in `X-API-Key` header
- **Privacy**: Only safe fields (amount, merchant, currency, type) leave iPhone
- **Secrets**: All tokens in `.env`, excluded from git

## Docker Services

| Service | Port | Description |
|---------|------|-------------|
| app (Firefly III) | 8080 | Budget dashboard |
| db (MariaDB) | 3306 (internal) | Database |
| auto-budget-api | 5000 | Transaction API |

## Logs

```bash
docker compose -f server/docker/docker-compose.yml logs -f auto-budget-api
```
