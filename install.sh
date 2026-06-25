#!/usr/bin/env bash
# BAW One-Line Installer — bare metal & Docker
# Usage: curl -fsSL https://raw.githubusercontent.com/cornreform/baw-agent-platform/main/install.sh | bash
set -e

BAW_DIR="$HOME/BAW"
GREEN='\033[1;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

echo -e "${BOLD}BAW Installer${NC}"
echo ""

# ── Clone ──
if [ -d "$BAW_DIR/.git" ]; then
    echo -e "  ${YELLOW}⟳${NC} Pulling latest..."
    cd "$BAW_DIR" && git pull origin main
else
    echo -e "  ${YELLOW}↓${NC} Cloning..."
    git clone https://github.com/cornreform/baw-agent-platform.git "$BAW_DIR"
fi
cd "$BAW_DIR"

# ── Venv ──
if [ ! -d "$BAW_DIR/venv" ]; then
    echo -e "  ${YELLOW}⟳${NC} Creating venv..."
    python3 -m venv venv
fi
source venv/bin/activate

# ── Dependencies ──
echo -e "  ${YELLOW}⟳${NC} Installing dependencies..."
pip install -r requirements.txt --quiet 2>&1 | tail -1

# ── Config ──
mkdir -p ~/.baw
[ ! -f ~/.baw/SOUL.md ] && cp SOUL.md ~/.baw/
[ ! -f ~/.baw/config.yaml ] && cp config.sample.yaml ~/.baw/config.yaml

# ── Symlink (case-sensitivity fix) ──
ln -sf "$BAW_DIR" ~/baw

# ── CLI Wrapper ──
sudo tee /usr/local/bin/baw > /dev/null << 'WRAPPER'
#!/bin/bash
BAW_DIR="${BAW_HOME:-$HOME/BAW}"
cd "$BAW_DIR" || exit 1
export PYTHONPATH="$BAW_DIR:$PYTHONPATH"
case "${1:-}" in
  ""|chat|tui-chat|status|models|config|router|soul|logs|dashboard|setup|memory|todo|tools|sessions|evolve|court|skill|restart|rebuild|self-test|preflight)
    exec "$BAW_DIR/venv/bin/python3" -m cli.main "$@" ;;
  *)
    exec "$BAW_DIR/venv/bin/python3" "$BAW_DIR/baw" "$@" ;;
esac
WRAPPER
sudo chmod 755 /usr/local/bin/baw

# ── Systemd Service ──
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/baw.service << SERVICE
[Unit]
Description=BAW Agent Platform
After=network.target
[Service]
Type=simple
ExecStart=%h/BAW/venv/bin/python3 %h/BAW/baw-bot --config %h/.baw/config.yaml
Restart=always
RestartSec=30  # slower to avoid Telegram rate limit death loop
WorkingDirectory=%h/BAW
EnvironmentFile=-%h/.baw/.env
[Install]
WantedBy=default.target
SERVICE
systemctl --user daemon-reload

# ── Done ──
echo ""
echo -e "  ${GREEN}✅ BAW installed!${NC}"
echo ""
echo -e "  Next:  ${BOLD}baw --setup${NC}         (configure API keys + model)"
echo -e "         ${BOLD}systemctl --user enable --now baw${NC}  (start Telegram bot)"
echo ""