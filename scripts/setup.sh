#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "========================================"
echo "  Auto-Budget Setup"
echo "========================================"
echo ""

# 1. Check Python
echo "[1/6] Checking Python..."
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 is required. Install it from https://python.org"
    exit 1
fi
python3 --version

# 2. Create virtual environment
echo ""
echo "[2/6] Setting up virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  Created virtual environment"
else
    echo "  Virtual environment already exists"
fi
source venv/bin/activate

# 3. Install dependencies
echo ""
echo "[3/6] Installing Python dependencies..."
pip install -r requirements.txt --quiet

# 4. Create data and logs directories
echo ""
echo "[4/6] Creating data directories..."
mkdir -p data logs

# 5. Set up .env file
echo ""
echo "[5/6] Setting up environment..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "  Created .env from template"
    echo "  --> Edit .env and add your ANTHROPIC_API_KEY"
else
    echo "  .env already exists"
fi

# 6. Set up Docker environment for Firefly III
echo ""
echo "[6/6] Setting up Firefly III Docker config..."
if [ ! -f "docker/.env" ]; then
    # Generate a random APP_KEY
    APP_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(24)[:32])")
    sed "s/CHANGE_ME_EXACTLY_32_CHARACTERS!/$APP_KEY/" docker/.env.example > docker/.env
    echo "  Created docker/.env with random APP_KEY"
fi
if [ ! -f "docker/.db.env" ]; then
    cp docker/.db.env.example docker/.db.env
    echo "  Created docker/.db.env"
fi

echo ""
echo "========================================"
echo "  Setup Complete!"
echo "========================================"
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. REQUIRED: Grant Full Disk Access to your terminal app:"
echo "   System Settings > Privacy & Security > Full Disk Access"
echo "   Add: Terminal.app (or iTerm, VS Code, etc.)"
echo ""
echo "2. Add your Anthropic API key:"
echo "   Edit .env and set ANTHROPIC_API_KEY=sk-ant-..."
echo ""
echo "3. Start Firefly III:"
echo "   cd docker && docker compose up -d"
echo "   Then open http://localhost:8080 and:"
echo "   a) Create an admin account"
echo "   b) Go to Profile > OAuth > Personal Access Tokens"
echo "   c) Create a token and add it to config/config.yaml"
echo ""
echo "4. Discover your bank's SMS format:"
echo "   source venv/bin/activate"
echo "   python3 scripts/discover_format.py"
echo ""
echo "5. Run your first sync:"
echo "   python3 -m src.sync"
echo ""
echo "6. Set up automatic background sync:"
echo "   cp scripts/com.autobudget.sync.plist ~/Library/LaunchAgents/"
echo "   launchctl load ~/Library/LaunchAgents/com.autobudget.sync.plist"
