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

# ── 2. Run the job, retrying the whole invocation ───────────────────────────
# The target's own internals may still hit a transient Drive read; a fresh
# invocation usually clears it, so retry the whole thing rather than fail the day.
rc=1
for attempt in 1 2 3; do
    log "run attempt $attempt"
    "${INTERP[@]}" "$TARGET" "${TARGET_ARGS[@]}" >> "$LOG" 2>&1
    rc=$?
    if [ $rc -eq 0 ]; then
        log "run succeeded (attempt $attempt)"
        touch "$STAMP"          # success stamp drives the staleness watchdog
        break
    fi
    log "run FAILED rc=$rc (attempt $attempt)"
    [ $attempt -lt 3 ] && sleep $((attempt * 60))   # 60s, then 120s
done

if [ $rc -ne 0 ]; then
    log "ERROR: all attempts failed (rc=$rc)"
    notify "$LABEL failed" "All 3 attempts failed (exit $rc). See ~/Library/Logs/${LABEL}.log"
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
