#!/bin/bash
# QB A7S — Media Hub Setup
# Run AFTER provision.sh: bash ~/qb-a7s-setup/media-hub.sh
#
# Sets up: Kodi, VLC, audio (PipeWire + USB dongle), GPU acceleration
# Also prepares the board for BAW deployment

set -euo pipefail

echo "=========================================="
echo " Media Hub + BAW Setup"
echo "=========================================="

# ── 1. Audio subsystem (PipeWire) ──
echo ""
echo "=== Audio Setup ==="

# Check if USB audio dongle exists
if lsusb 2>/dev/null | grep -qiE "audio|usb audio|cm108|pcm"; then
    echo "USB audio device detected."
    HAS_USB_AUDIO=true
else
    echo "No USB audio device detected. Install after plugging one in:"
    echo "  sudo apt install -y pipewire pipewire-pulse wireplumber"
    HAS_USB_AUDIO=false
fi

# Install PipeWire (better than PulseAudio for modern audio stack)
sudo apt install -y pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth || true

# Start PipeWire
systemctl --user enable --now pipewire pipewire-pulse 2>/dev/null || true
systemctl --user enable --now wireplumber 2>/dev/null || true

# Disable PulseAudio if it exists
if systemctl --user is-enabled pulseaudio 2>/dev/null; then
    systemctl --user mask pulseaudio 2>/dev/null || true
fi

echo "Audio: PipeWire enabled."
echo "  - USB audio dongle: auto-detected when plugged in"
echo "  - Bluetooth speaker: pair via KDE Bluetooth settings"
echo "  - Test: speaker-test -c 2 -l 1"

# ── 2. Media players ──
echo ""
echo "=== Media Players ==="

# Install VLC (lightweight, works with hardware GPU decoding)
sudo apt install -y vlc vlc-bin
echo "VLC installed."

# Install Kodi (full media center, HDMI CEC control)
sudo apt install -y kodi kodi-peripheral-joystick || true
echo "Kodi installed."

# ── 3. GPU acceleration for video ──
echo ""
echo "=== GPU Acceleration ==="

# The KDE image already has proper GPU drivers for PowerVR BXM-4-64
# Install VA-API / VDPAU drivers for hardware video decode
sudo apt install -y \
    mesa-va-drivers mesa-vdpau-drivers \
    i965-va-driver 2>/dev/null || true  # may not exist on ARM, that's fine

# Check if GPU drivers are loaded
echo "GPU info:"
if command -v glxinfo &>/dev/null; then
    DISPLAY=:0 glxinfo -B 2>/dev/null | grep -i "renderer" || echo "  (no display connected)"
else
    echo "  (glxinfo not available)"
fi

# ── 4. Enable KDE autologin (for headless/media center use) ──
echo ""
echo "=== KDE Autologin ==="
if [ -f /etc/sddm.conf ]; then
    sudo sed -i 's/^#?User=.*/User=radxa/' /etc/sddm.conf 2>/dev/null || true
    sudo sed -i 's/^#?Session=.*/Session=plasma/' /etc/sddm.conf 2>/dev/null || true
    echo "KDE autologin enabled for user radxa."
    echo "(Kodi can be set to auto-launch in KDE settings)"
else
    echo "Note: KDE autologin not configured (SDDM config not found)."
    echo "On first desktop boot, configure in System Settings → Login Screen."
fi

# ── 5. Open firewall for media services ──
echo ""
echo "=== Firewall (Media) ==="
# DLNA/UPnP (for media sharing)
sudo ufw allow 1900/udp comment 'UPnP/SSDP' 2>/dev/null || true
# Kodi web interface
sudo ufw allow 8080/tcp comment 'Kodi web' 2>/dev/null || true
# VLC streaming
sudo ufw allow 4212/tcp comment 'VLC streaming' 2>/dev/null || true
echo "Media ports opened in firewall."

# ── 6. Install Docker Compose (for BAW) ──
echo ""
echo "=== Docker Compose ==="
if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
    echo "Installing Docker Compose plugin..."
    sudo apt install -y docker-compose-plugin 2>/dev/null || \
        sudo apt install -y docker-compose 2>/dev/null || true
fi

# ── 7. Display info ──
echo ""
echo "=========================================="
echo " MEDIA HUB SETUP COMPLETE"
echo "=========================================="
echo ""
echo "Audio:"
echo "  - USB dongle: plug into USB-A port"
echo "  - Bluetooth: pair via KDE Bluetooth applet"
echo "  - Test: speaker-test -c 2 -l 1"
echo ""
echo "Video output:"
echo "  - Connect USB-C to HDMI/DP cable to the DATA USB-C port"
echo "    (the one nearest the board edge, NOT the power-only port)"
echo ""
echo "Media players:"
echo "  - Kodi:     kodi   (full media center)"
echo "  - VLC:      vlc    (lightweight player)"
echo ""
echo "BAW deployment (next step):"
echo "  BAW will be deployed in its own Docker container"
echo "  Ready when you are — Sunny."
echo ""
