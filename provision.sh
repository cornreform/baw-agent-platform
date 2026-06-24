#!/bin/bash
# QB A7S — Main Provisioning
# Run FIRST after initial SSH: bash ~/qb-a7s-setup/provision.sh

set -euo pipefail

HOSTNAME="qb-a7s"
TIMEZONE="Asia/Hong_Kong"
USER_PW="159357159"
NEW_USER="sunnycsl"

echo "=========================================="
echo " QB A7S — Main Provisioning"
echo "=========================================="

# ── 1. Create user sunnycsl (instead of renaming, which fails when logged in) ──
echo ""
echo "Creating user $NEW_USER..."
if id "$NEW_USER" &>/dev/null; then
    echo "User $NEW_USER already exists."
    echo "$NEW_USER:$USER_PW" | sudo chpasswd
else
    sudo useradd -m -s /bin/bash -G sudo,adm,dialout,audio,video,plugdev,gpio,i2c "$NEW_USER"
    echo "$NEW_USER:$USER_PW" | sudo chpasswd
    # Copy SSH authorized_keys from radxa
    if [ -f ~/.ssh/authorized_keys ]; then
        sudo cp ~/.ssh/authorized_keys /home/$NEW_USER/.ssh/
        sudo chown -R $NEW_USER:$NEW_USER /home/$NEW_USER/.ssh
    fi
    echo "User $NEW_USER created with sudo access."
fi

# ── 2. Set passwords ──
echo ""
echo "Setting passwords..."
echo "radxa:$USER_PW" | sudo chpasswd
echo "$NEW_USER:$USER_PW" | sudo chpasswd
echo "Passwords set (radxa + $NEW_USER = $USER_PW)."

# ── 3. Ensure sudo + adm groups for both users (docker added after install) ──
for grp in sudo adm dialout audio video plugdev gpio i2c render; do
    getent group $grp >/dev/null 2>&1 && sudo usermod -aG $grp radxa 2>/dev/null || true
done
for grp in sudo adm dialout audio video plugdev gpio i2c render; do
    getent group $grp >/dev/null 2>&1 && sudo usermod -aG $grp "$NEW_USER" 2>/dev/null || true
done

# ── 4. Set hostname ──
echo "Setting hostname to $HOSTNAME..."
echo "$HOSTNAME" | sudo tee /etc/hostname > /dev/null
sudo sed -i "s/127.0.1.1.*radxa/127.0.1.1\t$HOSTNAME/g" /etc/hosts 2>/dev/null || true

# ── 4. Timezone ──
echo "Setting timezone to $TIMEZONE..."
sudo timedatectl set-timezone "$TIMEZONE" 2>/dev/null || \
    sudo ln -sf "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime

# ── 5. System update ──
echo "Updating system..."
sudo apt update -qq
sudo apt upgrade -y

# ── 6. Essential packages ──
echo "Installing core packages..."
sudo apt install -y \
    curl wget git vim htop tmux tree unzip \
    net-tools ufw fail2ban \
    iotop sysstat lsof nmap \
    ca-certificates software-properties-common \
    python3-pip python3-venv \
    avahi-daemon chrony

# ── 7. Docker ──
if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sudo bash
    sudo usermod -aG docker radxa
    sudo usermod -aG docker "$NEW_USER"
    sudo systemctl enable docker
fi

# ── 8. SSH key (paste your key if desired) ──
echo ""
echo "Optionally paste your SSH public key (then Ctrl+D):"
echo "(Press Enter to skip — password login stays enabled)"
read -r -t 5 SSH_KEY 2>/dev/null || true
if [ -n "$SSH_KEY" ]; then
    echo "$SSH_KEY" >> ~/.ssh/authorized_keys
    chmod 600 ~/.ssh/authorized_keys
fi

# ── 9. Firewall (nftables — UFW broken on Bullseye kernel) ──
echo "Setting up nftables firewall..."
sudo tee /etc/nftables.conf << "EOF" > /dev/null
#!/usr/sbin/nft -f
flush ruleset
table inet filter {
    chain input {
        type filter hook input priority 0; policy drop;
        ct state established,related accept
        ct state invalid drop
        iif lo accept
        tcp dport ssh accept
    }
    chain forward {
        type filter hook forward priority 0; policy drop;
    }
    chain output {
        type filter hook output priority 0; policy accept;
    }
}
EOF
sudo systemctl enable --now nftables 2>/dev/null || true
sudo nft -f /etc/nftables.conf 2>&1 || echo "nftables: apply on next reboot"
echo "Firewall: SSH only (nftables)."

# ── 10. SSH config (keep password auth, no root) ──
sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config 2>/dev/null || \
    echo "PermitRootLogin no" | sudo tee -a /etc/ssh/sshd_config > /dev/null
sudo systemctl restart sshd
echo "SSH ready (root disabled, password login enabled)."

# ── 11. Swap ──
if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab > /dev/null
    echo "2GB swap created."
fi

# ── 12. Enable avahi ──
sudo systemctl enable --now avahi-daemon 2>/dev/null || true

# ── 13. Enable systemd linger (keep user services alive after logout) ──
sudo loginctl enable-linger radxa 2>/dev/null || true
sudo loginctl enable-linger "$NEW_USER" 2>/dev/null || true

# ── 14. Enable hardware monitoring ──
sudo apt install -y lm-sensors 2>/dev/null || true

# ── 14. Tailscale ──
echo "Installing Tailscale..."
if ! command -v tailscale &>/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sudo sh
    sudo systemctl enable --now tailscaled
    echo ""
    echo "Tailscale installed."
    echo ""
    echo "To authenticate:"
    echo "  sudo tailscale up"
    echo "  Then open the URL printed above in your browser."
fi

# ── 15. BAW bare metal deployment ──
echo ""
echo "=== BAW Bare Metal Deployment ==="
BAW_DIR="$HOME/BAW"
if [ ! -d "$BAW_DIR" ]; then
    echo "Cloning BAW agent platform..."
    git clone https://github.com/CornReform/baw-agent-platform.git "$BAW_DIR" 2>&1 || \
        echo "BAW clone failed. Retry later: git clone https://github.com/CornReform/baw-agent-platform.git ~/BAW"
else
    echo "BAW repo already exists at $BAW_DIR"
    cd "$BAW_DIR" && git pull 2>&1 | tail -1
fi

if [ -d "$BAW_DIR" ]; then
    echo "Setting up BAW Python venv..."
    cd "$BAW_DIR"
    if [ ! -d venv ]; then
        python3 -m venv venv
    fi
    source venv/bin/activate
    pip install --upgrade pip 2>&1 | tail -1
    # Install all deps, skip any that fail (e.g. pymupdf4llm on Python 3.9)
    pip install -r requirements.txt 2>&1 | grep -E "Successfully|ERROR" | tail -5 || true
    pip install requests httpx websocket-client croniter pyyaml python-telegram-bot aiohttp beautifulsoup4 lxml html2text markdownify pillow numpy markdown jinja2 psutil 2>&1 | tail -2
    if [ -f requirements-dev.txt ]; then
        pip install -r requirements-dev.txt 2>&1 | tail -1 || true
    fi
    # Fix Python 3.9 compat: add __future__ annotations for | None syntax
    for f in $(grep -rln "| None" core/ tools/ cli/ --include="*.py" 2>/dev/null); do
        if ! grep -q "from __future__ import annotations" "$f"; then
            sed -i "1s/^/from __future__ import annotations\n/" "$f"
        fi
    done
    # Fix Python 3.9 compat: get_event_loop -> get_running_loop
    sed -i 's/asyncio\.get_event_loop()\.run_in_executor/asyncio.get_running_loop().run_in_executor/' \
        core/messaging/telegram_async.py 2>/dev/null || true
    # Fix Python 3.9 compat: set event loop in background threads
    for _file in core/messaging/telegram.py; do
        if [ -f "$_file" ]; then
            # Add import if missing
            if ! grep -q "^import asyncio$" "$_file" 2>/dev/null; then
                sed -i '1s/^/import asyncio\n/' "$_file"
            fi
            # Wrap threaded handlers with event loop setup
            sed -i 's/def _process_message(self, \(.*\)):/def _process_message(self, \1):\n        asyncio.set_event_loop(asyncio.new_event_loop())/' "$_file"
            sed -i 's/def _process_image_file(self, \(.*\)):/def _process_image_file(self, \1):\n        asyncio.set_event_loop(asyncio.new_event_loop())/' "$_file"
            sed -i 's/def _process_document_file(self, \(.*\)):/def _process_document_file(self, \1):\n        asyncio.set_event_loop(asyncio.new_event_loop())/' "$_file"
            sed -i 's/def _process_voice_file(self, \(.*\)):/def _process_voice_file(self, \1):\n        asyncio.set_event_loop(asyncio.new_event_loop())/' "$_file"
            sed -i 's/def _handle_update(self, \(.*\)):/def _handle_update(self, \1):\n        asyncio.set_event_loop(asyncio.new_event_loop())/' "$_file" 2>/dev/null || true
        fi
    done 2>/dev/null || true
    # Fix Python 3.9 compat: default to _chat_response instead of _run_baw
    # (prevents memory-save loop taking over all conversations)
    sed -i 's/return self\._run_baw(text, chat_id=chat_id)/return self._chat_response(text, chat_id=chat_id)/' \
        core/messaging/__init__.py 2>/dev/null || true
    deactivate
    
    # Create BAW config directory
    mkdir -p "$HOME/.baw"
    
    # Create systemd user service for BAW
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/baw.service" << 'BAWSERVICE'
[Unit]
Description=BAW Agent Platform (Bare Metal)
After=network.target

[Service]
Type=simple
EnvironmentFile=%h/.baw/telegram.env
EnvironmentFile=%h/.baw/.env
ExecStart=%h/BAW/venv/bin/python3 %h/BAW/baw-bot --config %h/.baw/config.yaml
Restart=on-failure
RestartSec=5
WorkingDirectory=%h/BAW

[Install]
WantedBy=default.target
BAWSERVICE

    systemctl --user daemon-reload
    echo "BAW bare metal:"
    echo "  Repo:    $BAW_DIR"
    echo "  Venv:    $BAW_DIR/venv"
    echo "  Config:  $HOME/.baw/"
    echo "  Service: systemctl --user enable --now baw"
    echo ""
    echo "Nexi migration:"
    echo "  Copy ~/nexi-migration-pack/ content -> ~/.baw/ after first BAW setup"
fi

# ── Next steps ──
echo ""
echo "=========================================="
echo " PROVISIONING COMPLETE"
echo "=========================================="
echo ""
echo "Summary:"
echo "  User:       $NEW_USER / (password set)"
echo "  Hostname:   $HOSTNAME"
echo "  Tailscale:  installed — run 'sudo tailscale up' to auth"
echo "  BAW:        bare metal at ~/BAW (systemd user service baw.service)"
echo "  Services:   Docker, avahi, fail2ban"
echo "  Timezone:   $TIMEZONE"
echo ""
echo "Next steps (in order):"
echo "  bash ~/qb-a7s-setup/travel-router.sh"
echo "  bash ~/qb-a7s-setup/media-hub.sh"
echo "  systemctl --user enable --now baw         # Start BAW"
echo "  cp -r ~/nexi-migration-pack/* ~/.baw/     # Nexi migration"
echo "  sudo reboot"
echo ""
echo "After reboot: ssh $NEW_USER@$HOSTNAME.local"
