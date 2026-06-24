# Radxa QB A7S — Sticky Setup Kit

QB A7S (Allwinner A733, 4GB+) 多用途 SBC：
- **Travel router** — 酒店插 Ethernet，share WiFi
- **Media hub** — 駁電視 4K60 播片 + audio (USB dongle/BT)
- **BAW 獨立 Host** — 脫離 Hermes 獨立運行 BAW Agent Platform

---

## Files

| File | Purpose |
|------|---------|
| `radxa-a733_bullseye_kde_r6.img` (6.4G) | Radxa OS Desktop (KDE Plasma) |
| `flash.sh` | Flash OS to microSD + 預配 WiFi/SSH |
| `provision.sh` | 全自動系統設定（user、Docker、firewall、BAW） |
| `travel-router.sh` | Ethernet→WiFi hotspot（有安全 guard） |
| `media-hub.sh` | Kodi、VLC、PipeWire audio |

---

## Workflow

```
flash.sh → 插卡 boot機 → provision.sh → reboot → BAW 用得
```

---

## Step 1: Flash microSD

```bash
# Insert card → check device
lsblk

# Flash + pre-configure (scripts auto-copied to target)
sudo ./flash.sh /dev/sdX "WiFiSSID" "WiFiPassword"
```

Flash auto-does:
- Write 6.4G KDE image (detects correct partition: 3 not 2)
- `before.txt`: WiFi auto-connect + SSH enable + avahi(mDNS)
- Copy `provision.sh` + `travel-router.sh` + `media-hub.sh` to `/home/radxa/qb-a7s-setup/`

> 💡 **Path detection**: `flash.sh` auto-detects its own directory. Put all files in the same folder and run `sudo ./flash.sh ...` from there. No hardcoded host paths.

---

## Step 2: First Boot

```bash
# Wait 2-3 min for first boot (resize SD card + KDE init)
ping qb-a7s.local

# SSH in (password: radxa)
ssh radxa@qb-a7s.local
```

> ⚠️ First boot may take 5+ min on large SD cards. The default hostname is `radxa-cubie-a7s` if mDNS not yet resolved.

---

## Step 3: Provision

```bash
ssh radxa@192.168.1.x    # find IP from router

cd ~/qb-a7s-setup
bash provision.sh
```

### provision.sh 自動做嘅嘢：

| Step | What | Details |
|------|------|---------|
| 1 | **Create user** | `sunnycsl` with sudo + groups (skip docker if not exist) |
| 2 | **Set passwords** | `radxa` + `sunnycsl` = same password |
| 3 | **Hostname** | `qb-a7s` |
| 4 | **Timezone** | `Asia/Hong_Kong` |
| 5 | **System update** | `apt update && apt upgrade` |
| 6 | **Packages** | curl, git, docker, nmap, ufw→nftables, tailscale, ... |
| 7 | **Docker** | Install + add users to docker group (after docker exists) |
| 8 | **Firewall** | nftables — SSH only (NOT UFW — broken on Bullseye kernel) |
| 9 | **SSH** | Root disabled, password login kept |
| 10 | **Swap** | 2GB swap file |
| 11 | **Linger** | `loginctl enable-linger` (Keeps BAW alive after SSH logout) |
| 12 | **Tailscale** | Installed. Run `sudo tailscale up` to auth after reboot |
| 13 | **BAW** | Clone repo → venv → install deps (skip failures on Py3.9) |
| 14 | **Py3.9 compat** | Auto-add `from __future__ import annotations` for `X \| None` syntax |
| 15 | **BAW systemd** | `baw.service` — venv Python + env files, restart on failure |

---

## Step 4: Reboot & BAW

```bash
sudo reboot

# After reboot: SSH with new user
ssh sunnycsl@qb-a7s.local

# Auth Tailscale
sudo tailscale up

# Start BAW
systemctl --user enable --now baw

# Check status
systemctl --user status baw
journalctl --user -u baw -f
```

BAW runs **bare metal** — systemd user service, auto-restart on crash.

> 🔑 BAW reads config from `~/.baw/config.yaml`, API keys from `~/.baw/.env` + `~/.baw/telegram.env`

---

## Step 5: Nexi Migration

```bash
# Copy Nexi personality/memory/skills into BAW
cp -r ~/nexi-migration-pack/* ~/.baw/

# Restart BAW to load new config
systemctl --user restart baw
```

---

## Travel Router

```bash
# Enable (only works with Ethernet plugged in — safety guard)
bash ~/travel-router.sh enable

# Disable after use
bash ~/travel-router.sh disable

# Check status
bash ~/travel-router.sh status
```

> ⚠️ Hotspot SSID: `QB-A7S-Travel` / Password: `travel1234`
> ⚠️ Do NOT enable while WiFi client is active (same wlan0 interface)
> ⚠️ Power cycle to clear hotspot config if network drops

---

## Media Hub

```bash
bash ~/media-hub.sh
```

| Component | Setup |
|-----------|-------|
| **Video out** | USB-C → HDMI/DP (use DATA port, not power port) |
| **Audio** | USB audio dongle (USB-A) or Bluetooth speaker |
| **Kodi** | `kodi` |
| **VLC** | `vlc` |

> ⚠️ PipeWire has dependency issues on Bullseye backports — install manually if needed

---

## Known Issues & Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| **`X \| None` type error** | Python 3.9 doesn't support `T \| None` syntax | Auto-fixed by provision.sh (`__future__` annotations) |
| **UFW fails** | nftables compat broken on Bullseye kernel | provision.sh uses nftables instead |
| **BAW keeps restarting** | systemd user manager killed on SSH logout | `loginctl enable-linger` (auto-applied) |
| **WiFi lost after hotspot** | nmcli profile conflict | Power cycle board (fixes in travel-router.sh) |
| **`docker` group missing** | Docker not installed yet at user creation | provision.sh skips docker group, adds it after Docker install |
| **`Cannot resolve host` warnings** | Hostname changed before /etc/hosts update | Cosmetic only — harmless |

---

## Credentials (default)

| Item | Value |
|------|-------|
| **WiFi** | Set during flash.sh |
| **First boot user** | `radxa` / `radxa` |
| **After provision** | `sunnycsl` + `radxa` / same password (set in provision.sh) |
| **Hostname** | `qb-a7s.local` |
| **Tailscale** | `100.92.234.107` (after auth) |
| **BAW** | Telegram `@BAWtestonlybot` |

---

## Specs

| Item | Value |
|------|-------|
| **SoC** | Allwinner A733 (2×A76 + 6×A55) |
| **GPU** | Imagination PowerVR BXM-4-64 MC1 |
| **RAM** | 4GB DDR5 |
| **Storage** | microSD (boot) + NVMe via PCIe (~600MB/s) |
| **Network** | GigE, WiFi 6, BT 5.4 |
| **Video** | USB-C DP-out 4K60 (no audio over DP — use USB dongle) |
| **Power** | 5V 3A USB-C PD (official Radxa 30W adapter) |
| **Size** | 51mm × 51mm |
