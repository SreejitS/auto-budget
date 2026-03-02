#!/bin/bash
set -euo pipefail

# Auto-Budget RPi Setup Script
# Run this on a fresh Raspberry Pi OS Lite (64-bit) installation.

echo "=== Auto-Budget RPi Setup ==="
echo ""

# 1. System updates
echo "[1/5] Updating system packages..."
sudo apt update && sudo apt upgrade -y

# 2. Install Docker
if ! command -v docker &> /dev/null; then
    echo "[2/5] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "  Docker installed. You may need to log out and back in for group changes."
else
    echo "[2/5] Docker already installed."
fi

# 3. Install Tailscale
if ! command -v tailscale &> /dev/null; then
    echo "[3/5] Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    echo "  Run 'sudo tailscale up' after setup to authenticate."
else
    echo "[3/5] Tailscale already installed."
fi

# 4. Set up project
echo "[4/5] Setting up project..."
PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_DIR"

# Create server .env from example if it doesn't exist
if [ ! -f server/.env ]; then
    cp server/.env.example server/.env
    # Generate a random API key
    API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/change-me-to-a-random-string/$API_KEY/" server/.env
    else
        sed -i "s/change-me-to-a-random-string/$API_KEY/" server/.env
    fi
    echo "  Generated API key in server/.env"
    echo "  API Key: $API_KEY"
    echo "  Save this key — you'll need it for the iPhone Shortcut."
fi

# Create Firefly III env files from examples if they don't exist
if [ ! -f server/docker/.env ]; then
    if [ -f docker/.env ]; then
        cp docker/.env server/docker/.env
    else
        echo "  WARNING: No docker/.env found. Create server/docker/.env manually."
    fi
fi

if [ ! -f server/docker/.db.env ]; then
    if [ -f docker/.db.env ]; then
        cp docker/.db.env server/docker/.db.env
    else
        echo "  WARNING: No docker/.db.env found. Create server/docker/.db.env manually."
    fi
fi

# Create config from example if it doesn't exist
if [ ! -f config/config.yaml ]; then
    cp config/config.yaml.example config/config.yaml
    echo "  Created config/config.yaml — update with your Firefly III token."
fi

# 5. Start Docker stack
echo "[5/5] Starting Docker stack..."
cd server/docker
docker compose up -d --build

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Run 'sudo tailscale up' and note your Tailscale IP"
echo "  2. Access Firefly III at http://<tailscale-ip>:8080"
echo "  3. Create admin account and Personal Access Token"
echo "  4. Update config/config.yaml with the token"
echo "  5. Update server/.env with the FIREFLY_API_TOKEN"
echo "  6. Restart: cd server/docker && docker compose restart auto-budget-api"
echo "  7. Test: curl http://localhost:5000/health"
echo ""
echo "API Key for iPhone Shortcut:"
grep AUTO_BUDGET_API_KEY server/.env | head -1
