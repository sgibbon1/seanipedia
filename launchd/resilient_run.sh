#!/bin/bash
# resilient_run.sh — local-disk launcher for scheduled jobs whose scripts live on
# the Google Drive (CloudStorage) mount.
#
# THE PROBLEM THIS SOLVES
# Google Drive's virtual filesystem serves many files as placeholders that must be
# fetched ("hydrated") on first read. While Drive is syncing, restarting, or wedged,
# that read fails with errno 11 EDEADLK — "Resource deadlock avoided". If launchd
# points straight at a script ON the Drive mount, bash cannot even READ the script,
# so it dies before a single line of the job's own retry logic runs. And because the
# job's logs also live on Drive, the failure often isn't logged either. Result: the
# job silently stops running for days (seen 2026-07-18 daily.py, 07-22 daily_brief,
# 07-26..08-03 natsec_jobs — a 10-day outage nobody noticed).
#
# THE FIX
# THIS script lives on the real local disk, so it ALWAYS starts. It then:
#   1. Waits for the Drive-hosted target script to become genuinely readable
#      (actually reads a byte — mere existence is not enough for a placeholder).
#   2. Runs the target, retrying the whole invocation on failure with backoff.
#   3. Logs to ~/Library/Logs (local disk), so logging never depends on Drive.
#   4. Checks a sentinel file for staleness and raises a macOS notification when a
#      job hasn't succeeded in too long — so a silent outage becomes visible.
#
# Usage:
#   resilient_run.sh <label> <target-script> [stale-days] [target-args...]
# The target may be a .sh (run with bash) or a .py (run with python3); any further
# arguments are passed through to it.
# Examples:
#   resilient_run.sh natsec-jobs "/.../natsec_jobs/run_daily.sh" 2
#   resilient_run.sh daily-generate "/.../seanipedia/daily.py" 2 --generate
#
# STALENESS: every successful run stamps ~/Library/Logs/.rr_ok_<label>. The next
# run compares that stamp's age against <stale-days> and notifies if the job has
# not SUCCEEDED in that long. This works for every job regardless of whether the
# job itself keeps sentinels (only natsec_jobs does), which is what makes the
# watchdog universal. Pass 0 to disable.

LABEL="${1:?usage: resilient_run.sh <label> <target> [stale-days] [target-args...]}"
TARGET="${2:?missing target script}"
STALE_DAYS="${3:-2}"
shift 3 2>/dev/null || shift $#
TARGET_ARGS=("$@")

STAMP="$HOME/Library/Logs/.rr_ok_${LABEL}"

# Pick the interpreter from the target's extension.
case "$TARGET" in
    *.py) INTERP=(/usr/bin/python3) ;;
    *)    INTERP=(/bin/bash) ;;
esac

LOG="$HOME/Library/Logs/${LABEL}.log"
mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

notify() {  # $1 title, $2 message — best-effort; never fatal
    /usr/bin/osascript -e "display notification \"$2\" with title \"$1\"" 2>/dev/null || true
}

log "=== $LABEL start ==="

# ── 0. Already succeeded today? ─────────────────────────────────────────────
# Backup launchd triggers (see the plists: a primary time plus later retries)
# exist so one bad Drive window doesn't cost a whole day. They must be no-ops
# once the day's run has actually SUCCEEDED, which the stamp tells us.
# RR_FORCE=1 overrides, for manual re-runs.
if [ "${RR_FORCE:-0}" != "1" ] && [ -f "$STAMP" ]; then
    if [ "$(date -r "$STAMP" +%F)" = "$(date +%F)" ]; then
        log "already succeeded today — backup trigger no-op"
        exit 0
    fi
fi

# ── 1. Wait for the Drive-hosted script to be READABLE ──────────────────────
# `-r` only checks permissions; a placeholder passes that and still fails to read.
# Reading one byte is the honest test that Drive has materialised the file.
readable=0
for attempt in $(seq 1 30); do          # 30 × 10s ≈ 5 minutes of patience
    if [ -f "$TARGET" ] && head -c 1 "$TARGET" >/dev/null 2>&1; then
        readable=1
        [ "$attempt" -gt 1 ] && log "target readable after $((attempt * 10))s"
        break
    fi
    sleep 10
done

if [ "$readable" -ne 1 ]; then
    log "ERROR: '$TARGET' never became readable (Drive not serving the file). Giving up."
    notify "$LABEL failed" "Google Drive never served the script. Job did not run."
    exit 1
fi

# ── 1b. WARM UP every other file the job will read ──────────────────────────
# The 2026-08-12 daily-brief failure: this launcher proved run_daily_brief.sh was
# readable, then python died reading .env — a DIFFERENT file, never gated. One
# hydrated file says nothing about its neighbours. So touch them all: reading a
# byte forces Drive to materialise each placeholder while we can still wait.
# Best-effort by design — a cold unrelated module must not abort the run, and the
# retry loop below is the real safety net.
PROJ_DIR="$(cd "$(dirname "$TARGET")" && pwd)"
PROJ="$(basename "$PROJ_DIR")"
warm_fail=0
for f in "$PROJ_DIR"/.env "$PROJ_DIR"/*.py "$PROJ_DIR"/*.sh; do
    [ -f "$f" ] || continue
    for w in 1 2 3; do
        head -c 1 "$f" >/dev/null 2>&1 && break
        [ "$w" -eq 3 ] && { log "warn: '$(basename "$f")' still not readable"; warm_fail=$((warm_fail+1)); }
        sleep 5
    done
done
[ "$warm_fail" -gt 0 ] && log "warm-up: $warm_fail file(s) never became readable"

# ── 1c. Keep a LOCAL copy of the secrets ────────────────────────────────────
# Scheduled jobs read ~/.config/<project>/.env first (local disk cannot throw
# EDEADLK). Refresh it here whenever Drive is cooperating, so editing the Drive
# .env stays the way secrets are managed and the local copy follows along. If
# Drive is down, the existing local copy carries the run.
if [ -f "$PROJ_DIR/.env" ] && head -c 1 "$PROJ_DIR/.env" >/dev/null 2>&1; then
    mkdir -p "$HOME/.config/$PROJ"
    if ! cmp -s "$PROJ_DIR/.env" "$HOME/.config/$PROJ/.env" 2>/dev/null; then
        cp "$PROJ_DIR/.env" "$HOME/.config/$PROJ/.env" && \
            chmod 600 "$HOME/.config/$PROJ/.env" && log "refreshed local .env copy"
    fi
fi

# ── 2. Run the job, retrying the whole invocation ───────────────────────────
# The target's own internals may still hit a transient Drive read; a fresh
# invocation usually clears it, so retry the whole thing rather than fail the day.
# Backoff must OUTLAST the outage, not sit inside it. The old 60s/120s spacing
# put all three attempts in a 3-minute span (17:01–17:04 on 2026-08-12) — a Drive
# hydration stall routinely runs longer than that, so every retry failed for the
# same reason. 2min / 10min / 30min straddles a typical stall instead.
RETRY_WAITS=(120 600 1800)
# RR_ATTEMPTS caps the attempts (default 4). The morning chain runs under this
# launcher with RR_ATTEMPTS=1: each of its steps already gets its own four
# attempts, so retrying the whole chain on top would compound into hours of
# waiting. A failed chain is retried by its next launchd trigger instead.
ATTEMPTS="${RR_ATTEMPTS:-4}"
rc=1
for attempt in $(seq 1 "$ATTEMPTS"); do
    log "run attempt $attempt"
    "${INTERP[@]}" "$TARGET" "${TARGET_ARGS[@]}" >> "$LOG" 2>&1
    rc=$?
    if [ $rc -eq 0 ]; then
        log "run succeeded (attempt $attempt)"
        touch "$STAMP"          # success stamp drives the staleness watchdog
        break
    fi
    log "run FAILED rc=$rc (attempt $attempt)"
    if [ $attempt -lt "$ATTEMPTS" ]; then
        wait_s=${RETRY_WAITS[$((attempt-1))]}
        log "waiting ${wait_s}s before retry"
        sleep "$wait_s"
    fi
done

if [ $rc -ne 0 ]; then
    log "ERROR: all attempts failed (rc=$rc)"
    notify "$LABEL failed" "All $ATTEMPTS attempt(s) failed (exit $rc). See ~/Library/Logs/${LABEL}.log"
fi

# ── 3. Staleness watchdog ───────────────────────────────────────────────────
# Independent of this run's outcome: if the job hasn't SUCCEEDED in a while, say so.
# This is what turns a silent multi-day outage into something you actually see.
if [ "$STALE_DAYS" -gt 0 ]; then
    if [ -f "$STAMP" ]; then
        age_days=$(( ( $(date +%s) - $(stat -f %m "$STAMP") ) / 86400 ))
        log "last success: ${age_days}d ago"
        if [ "$age_days" -ge "$STALE_DAYS" ]; then
            notify "$LABEL is stale" "Last successful run was ${age_days} days ago. See ~/Library/Logs/${LABEL}.log"
        fi
    else
        # No stamp yet: either the very first run under this launcher, or the job
        # has never succeeded since it was adopted. Only shout if THIS run failed
        # too — otherwise the stamp we just wrote covers it.
        [ $rc -ne 0 ] && notify "$LABEL has never succeeded" "No successful run recorded yet."
        log "no success stamp yet (first run under launcher)"
    fi
fi

log "=== $LABEL end (rc=$rc) ==="
exit $rc
