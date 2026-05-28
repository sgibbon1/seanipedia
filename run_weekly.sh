#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p logs

# ── Weekly gate ────────────────────────────────────────────────────────────────
# Run only if it has been 7 or more days since the last successful run.
SENTINEL="logs/.last_weekly_run"
if [ -f "$SENTINEL" ]; then
    LAST_RUN=$(cat "$SENTINEL")
    TODAY=$(date +%Y-%m-%d)
    DAYS_SINCE=$(python3 -c "from datetime import date; print((date.fromisoformat('$TODAY') - date.fromisoformat('$LAST_RUN')).days)")
    if [ "$DAYS_SINCE" -lt 7 ]; then
        echo "[INFO] Only ${DAYS_SINCE} day(s) since last run ($LAST_RUN) — skipping."
        exit 0
    fi
    echo "[INFO] ${DAYS_SINCE} day(s) since last run ($LAST_RUN) — proceeding."
fi

# ── DNS wait ───────────────────────────────────────────────────────────────────
# Fires on every resolv.conf change (network reconnect / wake-from-sleep).
# api.anthropic.com must resolve before we bother running.
echo "[INFO] Waiting 20s for DNS to stabilise after network reconnect..."
sleep 20
echo "[INFO] Checking DNS for api.anthropic.com..."
for i in $(seq 1 60); do
    if host api.anthropic.com > /dev/null 2>&1; then
        echo "[INFO] DNS ready after $((20 + i * 5))s."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "[ERROR] DNS unavailable after 320s — will retry on next network event."
        exit 1
    fi
    sleep 5
done

# ── Load env vars ──────────────────────────────────────────────────────────────
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# ── Run ────────────────────────────────────────────────────────────────────────
python3 weekly_report.py

# Only update the sentinel after a successful run
date +%Y-%m-%d > "$SENTINEL"
echo "[INFO] Weekly report complete. Sentinel updated."
