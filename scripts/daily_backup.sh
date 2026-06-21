#!/usr/bin/env bash
# BAW daily backup: config + memory + skills (with 7-day retention)
# Idempotent — safe to re-run any time.

set -euo pipefail

BAW_DIR="${BAW_DIR:-$HOME/.baw}"
BACKUP_DIR="$BAW_DIR/backups"
REPORT_DIR="$BAW_DIR/daily_reports"
STAMP="$(date +%Y%m%d_%H%M%S)"
DATE="$(date +%Y-%m-%d)"
ARCHIVE="$BACKUP_DIR/baw-daily-${DATE}.tar.gz"
REPORT="$REPORT_DIR/backup_${DATE}.json"
KEEP=7

mkdir -p "$BACKUP_DIR" "$REPORT_DIR"

# --- 1. Build archive ------------------------------------------------------
# Config files
CONFIG_FILES=(
    "$BAW_DIR/config.yaml"
    "$BAW_DIR/managed_config.yaml"
    "$BAW_DIR/SOUL.md"
    "$BAW_DIR/schedule.yaml"
)
CONFIG_ARGS=()
for f in "${CONFIG_FILES[@]}"; do
    [ -f "$f" ] && CONFIG_ARGS+=("$f")
done

# Compose tar. Use --transform so the archive root is clean: baw-backup/<files>
tar -czf "$ARCHIVE" \
    --transform "s,^${BAW_DIR}/,baw-backup/," \
    -C "$BAW_DIR" \
    config.yaml managed_config.yaml SOUL.md schedule.yaml \
    memory knowledge_graph.json \
    skills \
    2>/dev/null || {
        echo "[ERROR] tar failed" >&2
        exit 1
    }

# --- 2. Report size --------------------------------------------------------
SIZE_BYTES=$(stat -c%s "$ARCHIVE")
SIZE_MB=$(awk -v b="$SIZE_BYTES" 'BEGIN{printf "%.2f", b/1024/1024}')

# --- 3. Prune — keep last KEEP backups (matching daily pattern only) -------
# Match: baw-daily-YYYY-MM-DD*.tar.gz
ls -1t "$BACKUP_DIR"/baw-daily-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f -- "$old"
done

REMAINING=$(ls -1 "$BACKUP_DIR"/baw-daily-*.tar.gz 2>/dev/null | wc -l)

# --- 4. Write JSON report --------------------------------------------------
python3 - "$ARCHIVE" "$SIZE_BYTES" "$SIZE_MB" "$REMAINING" "$KEEP" "$DATE" "$REPORT" <<'PY'
import json, os, sys, time
archive, size_b, size_mb, remaining, keep, date, report = sys.argv[1:]
data = {
    "task": "daily-backup",
    "date": date,
    "archive": archive,
    "size_bytes": int(size_b),
    "size_mb": float(size_mb),
    "remaining_backups": int(remaining),
    "retention": int(keep),
    "contents": ["config.yaml", "managed_config.yaml", "SOUL.md", "schedule.yaml",
                 "memory/", "knowledge_graph.json", "skills/"],
    "ts": int(time.time()),
}
os.makedirs(os.path.dirname(report), exist_ok=True)
with open(report, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print(json.dumps(data, indent=2, ensure_ascii=False))
PY

echo "✅ daily-backup complete: $ARCHIVE ($SIZE_MB MB) — kept $REMAINING/$KEEP"
