#!/bin/bash
# Auto-budget sync wrapper
# Runs the sync directly. When called from Terminal.app (which has FDA),
# Python can read chat.db directly — no copy needed.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$PROJECT_DIR/logs/sync.log"

mkdir -p "$PROJECT_DIR/logs"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

log "=== Sync started ==="

cd "$PROJECT_DIR"
"$PROJECT_DIR/venv/bin/python3" -m src.sync >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

log "=== Sync finished (exit code: $EXIT_CODE) ==="
