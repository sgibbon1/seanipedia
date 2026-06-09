#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p logs

# ── Scheduling note ─────────────────────────────────────────────────────────────
# No day-of-week gate here.  weekly_report.py owns all calendar logic: it reports
# every completed Sun–Sat week that doesn't yet have a report (tracked in
# logs/.last_reported_week) and is idempotent — running it on the wrong day, or
# several times a day, generates only what's missing and otherwise does nothing.
# That lets this wrapper fire from many triggers (Sunday 9am, network reconnect,
# login) so a week is never skipped, without ever producing a duplicate report.

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
# .env lives on Google Drive (CloudStorage), which intermittently returns EDEADLK
# ("Resource deadlock avoided") when bash reads a not-yet-materialised cloud file
# after wake-from-sleep. Under `set -e` that single failure would abort the report.
# Retry a few times so a transient lock doesn't kill the run.
if [ -f .env ]; then
    set -a
    sourced=0
    for attempt in 1 2 3 4 5; do
        # shellcheck disable=SC1091
        if source .env 2>/dev/null; then sourced=1; break; fi
        echo "[WARN] .env source failed (attempt ${attempt}/5) — retrying in 3s..."
        sleep 3
    done
    set +a
    if [ "$sourced" -ne 1 ]; then
        echo "[ERROR] Could not source .env after 5 attempts — aborting; will retry next run."
        exit 1
    fi
fi

# ── Run ────────────────────────────────────────────────────────────────────────
# weekly_report.py records each reported week in logs/.last_reported_week itself,
# so there is no sentinel to update here.
python3 weekly_report.py
echo "[INFO] Weekly report run complete."
