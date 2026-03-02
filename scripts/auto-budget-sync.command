#!/bin/bash
# Auto-budget background sync
# This runs inside Terminal.app which has Full Disk Access.
# Added as a Login Item so it starts automatically at login.
# Syncs every 15 minutes in a loop.

PROJECT_DIR="/Users/sreejits/Dev/auto-budget"
LOG_FILE="$PROJECT_DIR/logs/sync.log"

mkdir -p "$PROJECT_DIR/logs"

echo "Auto-budget sync started. Runs every 15 minutes."
echo "You can minimize this window. Do not close it."
echo "Log: $LOG_FILE"
echo "---"

while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running sync..."
    cd "$PROJECT_DIR"
    "$PROJECT_DIR/venv/bin/python3" -m src.sync >> "$LOG_FILE" 2>&1 || true
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync done. Next run in 15 minutes."
    sleep 900
done
