#!/usr/bin/env bash
# BAW Bot — Deploy Script
# Installs systemd service for Telegram bot
#
# Updated 2026-06-08: Fixed env file check — was checking ~/.baw/.env
# but systemd uses ~/.baw/telegram.env (no 'export' prefix format)

set -euo pipefail

BAW_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_FILE="${BAW_DIR}/deploy/baw-telegram.service"
SERVICE_NAME="baw-telegram"
ENV_FILE="$HOME/.baw/telegram.env"

echo "📡 BAW Bot — Deploy"
echo "===================="
echo ""

# Check env
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Missing $ENV_FILE — create with TELEGRAM token first"
    echo "   echo 'BAW_TELEGRAM_TOKEN=\"your:token\"' >> $ENV_FILE"
    echo "   (No 'export' prefix — systemd EnvironmentFile format)"
    exit 1
fi

grep -q BAW_TELEGRAM_TOKEN "$ENV_FILE" 2>/dev/null || {
    echo "❌ BAW_TELEGRAM_TOKEN not set in $ENV_FILE"
    echo "   Expected format: BAW_TELEGRAM_TOKEN=\"your:token\""
    exit 1
}

# Also check the file doesn't have 'export' prefix (systemd doesn't support it)
if grep -q '^export ' "$ENV_FILE" 2>/dev/null; then
    echo "⚠️  Warning: $ENV_FILE has 'export' prefix lines."
    echo "   systemd's EnvironmentFile does NOT support 'export'."
    echo "   The service may fail to load the token."
    echo ""
    echo "   Fix with: sed -i 's/^export //' $ENV_FILE"
fi

# Install systemd service
echo "🔧 Installing systemd service..."
sudo cp "$SERVICE_FILE" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.service"
sudo systemctl restart "${SERVICE_NAME}.service"

# Show status
echo "📊 Status:"
sleep 2
sudo systemctl status "${SERVICE_NAME}.service" --no-pager | head -15

echo ""
echo "✅ BAW Telegram Bot deployed!"
echo "   Logs: sudo journalctl -u ${SERVICE_NAME}.service -f"
echo "   Stop: sudo systemctl stop ${SERVICE_NAME}.service"
echo "   Env:  $ENV_FILE"
