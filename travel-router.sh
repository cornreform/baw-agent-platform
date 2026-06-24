#!/bin/bash
# QB A7S — Travel Router (Ethernet → WiFi Hotspot)
# Run AFTER provision.sh: bash ~/qb-a7s-setup/travel-router.sh
#
# How it works:
# - Hotel/place has Ethernet (plug into QB A7S GigE port)
# - QB A7S creates a WiFi hotspot that shares that connection
# - Your phone/tablet/laptop connects to the hotspot
#
# Switching modes:
#   At home (normal WiFi client):   sudo systemctl start NetworkManager
#   Travel mode (Ethernet→WiFi AP): bash ~/qb-a7s-setup/travel-router.sh enable
#
# The hotspot is persistent — reboot and it re-enables automatically
# unless you disable it.

set -euo pipefail

ACTION="${1:-enable}"
HOTSPOT_SSID="QB-A7S-Travel"
HOTSPOT_PASS="travel1234"
WIFI_IFACE="wlan0"
ETH_IFACE="end0"  # Gigabit Ethernet on QB A7S

echo "=========================================="
echo " Travel Router Setup"
echo "=========================================="

# ── 1. Install dependencies ──
install_deps() {
    echo "Installing packages..."
    sudo apt install -y \
        network-manager \
        dnsmasq-base \
        iptables
}

# ── 2. Enable hotspot mode ──
enable_hotspot() {
    echo "Setting up WiFi hotspot..."
    echo "  SSID:     $HOTSPOT_SSID"
    echo "  Password: $HOTSPOT_PASS"
    echo "  Source:   $ETH_IFACE (Ethernet)"
    echo ""

    # Stop conflicting services
    sudo systemctl stop wpa_supplicant 2>/dev/null || true
    
    # Create the hotspot via NetworkManager
    # Remove existing connection with same name if any
    sudo nmcli con delete "$HOTSPOT_SSID" 2>/dev/null || true

    # Create hotspot
    sudo nmcli con add \
        type wifi \
        con-name "$HOTSPOT_SSID" \
        ifname "$WIFI_IFACE" \
        mode ap \
        ssid "$HOTSPOT_SSID"
    sudo nmcli con modify "$HOTSPOT_SSID" \
        wifi-sec.key-mgmt wpa-psk
    sudo nmcli con modify "$HOTSPOT_SSID" \
        wifi-sec.psk "$HOTSPOT_PASS"

    # Set up IP sharing (masquerade)
    sudo nmcli con modify "$HOTSPOT_SSID" \
        ipv4.method shared \
        ipv4.address 10.42.0.1/24

    # Enable IP forwarding
    echo "Enabling IP forwarding..."
    echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-router.conf > /dev/null
    sudo sysctl -w net.ipv4.ip_forward=1

    # Bring up hotspot
    sudo nmcli con up "$HOTSPOT_SSID"
    
    # Open firewall for NAT traffic
    sudo ufw allow in on $WIFI_IFACE 2>/dev/null || true
    sudo ufw route allow in on $WIFI_IFACE out on $ETH_IFACE 2>/dev/null || true

    echo ""
    echo "Hotspot is ACTIVE."
    echo "Connect your devices to WiFi: $HOTSPOT_SSID"
    echo "Password: $HOTSPOT_PASS"
    echo ""
}

# ── 3. Disable hotspot (revert to normal WiFi client) ──
disable_hotspot() {
    echo "Disabling hotspot..."
    sudo nmcli con down "$HOTSPOT_SSID" 2>/dev/null || true
    sudo nmcli con delete "$HOTSPOT_SSID" 2>/dev/null || true
    sudo ufw delete allow in on $WIFI_IFACE 2>/dev/null || true
    echo "Hotspot disabled. Re-enable normal WiFi:"
    echo "  sudo nmcli radio wifi on"
    echo "  sudo nmcli dev wifi connect <SSID> password <pass>"
}

case "$ACTION" in
    enable|start|on)
        install_deps
        enable_hotspot
        ;;
    disable|stop|off)
        disable_hotspot
        ;;
    status)
        if nmcli con show --active | grep -q "$HOTSPOT_SSID"; then
            echo "Hotspot ACTIVE — SSID: $HOTSPOT_SSID"
            echo "Clients connected:"
            arp -a 2>/dev/null | grep "10.42.0" || echo "  (none)"
        else
            echo "Hotspot INACTIVE."
        fi
        ;;
    *)
        echo "Usage: $0 {enable|disable|status}"
        echo ""
        echo "  enable  — Create WiFi hotspot (Ethernet → WiFi)"
        echo "  disable — Remove hotspot, restore normal WiFi"
        echo "  status  — Check if hotspot is running"
        exit 1
        ;;
esac

echo ""
echo "=========================================="
if [ "$ACTION" = "enable" ]; then
    echo "Hotspot stays active after reboot."
    echo "Disable later:  bash $0 disable"
fi
echo "=========================================="
