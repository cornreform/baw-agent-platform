#!/bin/bash
# Radxa QB A7S — flash + configure WiFi/SSH + deploy provision scripts
# Usage: sudo ./flash.sh /dev/sdX <wifi-ssid> <wifi-password>
# Example: sudo ./flash.sh /dev/sda "SunnyHome" "mypassword"
#
# WARNING: microSD card will be COMPLETELY WIPED

set -euo pipefail

IMG="$SCRIPT_DIR/radxa-a733_bullseye_kde_r6.img"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ $# -lt 3 ]; then
    echo "Usage: sudo $0 <device> <wifi-ssid> <wifi-password>"
    echo "  e.g.: sudo $0 /dev/sda \"MyWiFi\" \"secret123\""
    exit 1
fi

DEVICE="$1"
WIFI_SSID="$2"
WIFI_PASS="$3"

# Safety checks
if [ ! -b "$DEVICE" ]; then
    echo "ERROR: $DEVICE is not a block device"
    exit 1
fi

if [ ! -f "$IMG" ]; then
    echo "ERROR: Image not found at $IMG"
    exit 1
fi

# Confirm
echo "=========================================="
echo "Target device: $DEVICE ($(lsblk -ndo SIZE $DEVICE 2>/dev/null || echo '?'))"
echo "WiFi SSID:     $WIFI_SSID"
echo "Image:         $IMG ($(du -h "$IMG" | cut -f1))"
echo "=========================================="
echo ""
echo "WARNING: ALL DATA on $DEVICE will be DESTROYED!"
echo ""
read -p "Continue? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 1
fi

# Unmount any mounted partitions
echo "Unmounting $DEVICE partitions..."
for p in $(ls "${DEVICE}"* 2>/dev/null); do
    if mount | grep -q "$p"; then
        echo "  Unmounting $p..."
        sudo umount "$p" 2>/dev/null || true
    fi
done

# Flash the image with progress
echo "Flashing $IMG to $DEVICE..."
pv -pterb "$IMG" | sudo dd of="$DEVICE" bs=4M conv=fsync status=none 2>&1 || {
    # fallback without pv
    echo "pv not found, using dd directly..."
    sudo dd if="$IMG" of="$DEVICE" bs=4M conv=fsync status=progress
}
sync
echo "Flash complete."

# Wait for partition table to reload
sleep 3

# Find boot partition
BOOT_PART=""
for p in "${DEVICE}"1 "${DEVICE}"p1; do
    if [ -b "$p" ]; then BOOT_PART="$p"; break; fi
done

if [ -z "$BOOT_PART" ]; then
    echo "WARNING: No boot partition found. WiFi config SKIPPED."
    echo "After first boot, run: sudo nmtui  (or use nmcli)"
    exit 0
fi

# Mount boot partition
MNT=$(mktemp -d)
sudo mount "$BOOT_PART" "$MNT"

# --- Write WiFi config to before.txt ---
BEFORE_TXT="$MNT/before.txt"
if [ -f "$BEFORE_TXT" ]; then
    echo "Adding WiFi config to before.txt..."
    echo "connect_wi-fi $WIFI_SSID $WIFI_PASS" | sudo tee -a "$BEFORE_TXT" > /dev/null
else
    echo "connect_wi-fi $WIFI_SSID $WIFI_PASS" | sudo tee "$BEFORE_TXT" > /dev/null
fi
echo "before.txt content:"
cat "$BEFORE_TXT"

# --- Enable SSH + avahi via config.txt ---
CONFIG_TXT="$MNT/config.txt"

# Add ssh if not present
if [ -f "$CONFIG_TXT" ]; then
    if ! grep -q "^ssh" "$CONFIG_TXT" 2>/dev/null; then
        echo "ssh" | sudo tee -a "$CONFIG_TXT" > /dev/null
    fi
    if ! grep -q "^avahi" "$CONFIG_TXT" 2>/dev/null; then
        echo "avahi" | sudo tee -a "$CONFIG_TXT" > /dev/null
    fi
else
    echo -e "ssh\navahi" | sudo tee "$CONFIG_TXT" > /dev/null
fi

sudo umount "$MNT"
rmdir "$MNT"

# --- Find root partition (try 3 first for KDE image, then 2, then p3/p2) ---
ROOT_PART=""
for p in "${DEVICE}"3 "${DEVICE}"2 "${DEVICE}"p3 "${DEVICE}"p2; do
    if [ -b "$p" ]; then
        # Make sure it's actually the root (ext4, not vfat)
        FSTYPE=$(blkid -o value -s TYPE "$p" 2>/dev/null || echo "")
        if [ "$FSTYPE" = "ext4" ]; then
            ROOT_PART="$p"
            break
        fi
    fi
done

if [ -n "$ROOT_PART" ]; then
    ROOT_MNT=$(mktemp -d)
    sudo mount "$ROOT_PART" "$ROOT_MNT"
    
    # Copy provision scripts into home
    sudo mkdir -p "$ROOT_MNT/home/radxa/qb-a7s-setup"
    
    for script in provision.sh travel-router.sh media-hub.sh; do
        if [ -f "$SCRIPT_DIR/$script" ]; then
            sudo cp "$SCRIPT_DIR/$script" "$ROOT_MNT/home/radxa/qb-a7s-setup/"
            sudo chmod +x "$ROOT_MNT/home/radxa/qb-a7s-setup/$script"
        fi
    done
    
    # Copy README
    if [ -f "$SCRIPT_DIR/README.md" ]; then
        sudo cp "$SCRIPT_DIR/README.md" "$ROOT_MNT/home/radxa/qb-a7s-setup/"
    fi
    
    sudo chown -R 1000:1000 "$ROOT_MNT/home/radxa/qb-a7s-setup"
    
    echo "Provision scripts copied to /home/radxa/qb-a7s-setup/"
    
    sudo umount "$ROOT_MNT"
    rmdir "$ROOT_MNT"
fi

echo ""
echo "=========================================="
echo "DONE! microSD card ready."
echo ""
echo "1. Insert into QB A7S + power on"
echo "2. Wait ~3 min for first boot (auto-connects WiFi)"
echo "3. Find IP:"
echo "   ping qb-a7s.local"
echo "   or scan: sudo nmap -sn 192.168.1.0/24"
echo "4. SSH: ssh radxa@qb-a7s.local  (password: radxa)"
echo "5. Run: cd ~/qb-a7s-setup && bash provision.sh"
echo "=========================================="
