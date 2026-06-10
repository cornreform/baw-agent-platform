#!/usr/bin/env bash
# BAW Bot — Deploy Script (Docker)
# Installs systemd service for Docker Compose based BAW bot
set -euo pipefail

BAW_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_FILE="${BAW_DIR}/deploy/baw-docker.service"
SERVICE_NAME="baw-docker"
ENV_FILE="$HOME/.baw/telegram.env"

echo "📡 BAW Bot — Docker Deploy"
echo "=========================="
echo ""

# Check env
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Missing $ENV_FILE — create with BAW_TELEGRAM_TOKEN first"
    echo "   echo 'BAW_TELEGRAM_TOKEN=\"your:token\"' >> $ENV_FILE"
    exit 1
fi

grep -q BAW_TELEGRAM_TOKEN "$ENV_FILE" 2>/dev/null || {
    echo "❌ BAW_TELEGRAM_TOKEN not set in $ENV_FILE"
    exit 1
}

# Build Docker image
echo "🔨 Building Docker image..."
cd "$BAW_DIR"
docker compose build

# Stop old service if running
sudo systemctl stop "${SERVICE_NAME}" 2>/dev/null || true

# Install systemd service
echo "🔧 Installing systemd service..."
sudo cp "$SERVICE_FILE" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.service"
sudo systemctl start "${SERVICE_NAME}.service"

# Show status
echo "📊 Status:"
sleep 3
docker ps --filter "name=baw" --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"

echo ""
echo "✅ BAW Telegram Bot deployed (Docker)!"
echo "   Logs:  docker logs -f baw-telegram"
echo "   Stop:  sudo systemctl stop ${SERVICE_NAME}.service"
echo "   Env:   $ENV_FILE"
