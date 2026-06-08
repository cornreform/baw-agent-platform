#!/usr/bin/env bash
# BAW Bot — Deploy Script
# Installs systemd service for Telegram bot

set -euo pipefail

BAW_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_FILE="${BAW_DIR}/deploy/baw-telegram.service"
SERVICE_NAME="baw-telegram"

echo "📡 BAW Bot — Deploy"
echo "===================="
echo ""

# Check env
if [ ! -f "$HOME/.baw/.env" ]; then
    echo "❌ Missing $HOME/.baw/.env — create with TELEGRAM token first"
    echo "   echo 'export BAW_TELEGRAM_TOKEN=\"your:token\"' >> \$HOME/.baw/.env"
    exit 1
fi

grep -q BAW_TELEGRAM_TOKEN "$HOME/.baw/.env" 2>/dev/null || {
    echo "❌ BAW_TELEGRAM_TOKEN not set in $HOME/.baw/.env"
    exit 1
}

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
