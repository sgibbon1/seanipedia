#!/bin/bash
# morning_chain.sh — run the morning's dependent jobs IN ORDER, each only after
# the one it depends on has succeeded.
#
# WHY A CHAIN INSTEAD OF CLOCK TIMES
# These jobs used to be eight independent launchd entries spaced hours apart,
# and the spacing was the only thing enforcing their order. The clock is not a
# reliable guard on a laptop that sleeps — and on 2026-10-26 launchd's scheduler
# fell five hours out of step with the system clock, so parse's backup trigger
# landed in the same minute as generate every night, and on 10-30 the race
# deleted the freshly generated note. Ordering by dependency instead of by time
# makes all of that structurally impossible.
#
#   SPINE (each step needs the previous one; a failure stops the spine)
#     1. parse     yesterday's note → journal, outbox, brief archive, carryover
#     2. generate  today's Today.md (On This Day, quotes, carryover, study)
#     4. brief     email_scan, then the brief → ## Daily Intelligence Brief
#
#   SIDE / TAIL (always attempted; they shouldn't be held hostage by the brief)
#     3. jobs      scrape → SQLite → Sheet → ## Today's Jobs (needs the note to
#                  inject, but still scrapes without one)
#     5. weekly    completed Sun–Sat weeks — but only once parse succeeded,
#                  because Sunday's report must include Saturday's filed note
#     6. reindex   rebuild the quote deck from archive/Quotes (which weekly just
#                  moved new quotes into), then stamp their ^id block anchors.
#                  Nothing scheduled this before: the deck froze on 2026-08-16.
#
# HOW RE-RUNS STAY SAFE
# Every step goes through ~/scripts/resilient_run.sh under the SAME label its
# old standalone job used, so it keeps that job's retries (2/10/30 min), Drive
# warm-up, local .env sync, staleness alert — and its "already succeeded today"
# stamp. That stamp is what lets this chain fire three times a morning: on the
# second and third triggers every finished step is a no-op and only unfinished
# work runs. daily.py adds its own guards on top (parse refuses today's note,
# generate refuses to overwrite one) and a lock so they never overlap.
#
# Usage:
#   morning_chain.sh               # what launchd runs
#   morning_chain.sh --dry-run     # show what each step WOULD do; run nothing
#   morning_chain.sh --force-brief # re-run just the brief today (a same-day
#                                  # rescan: folds in mail since the first run)

set -uo pipefail     # NOT -e: a failed step is handled, not fatal

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
SEANIPEDIA="$(dirname "$SELF")"
AI_CODE="$(dirname "$SEANIPEDIA")"
RR="$HOME/scripts/resilient_run.sh"
VAULT_TODAY="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/vault/_inbox/Today.md"
LOCK="$HOME/Library/Logs/.morning-chain.lock"

DRY=0; FORCE_BRIEF=0
for a in "$@"; do
    case "$a" in
        --dry-run)     DRY=1 ;;
        --force-brief) FORCE_BRIEF=1 ;;
        *) echo "unknown option: $a"; exit 2 ;;
    esac
done

# ── One chain at a time ─────────────────────────────────────────────────────
# A trigger can fire while an earlier run is still waiting out a step's retries.
# Two chains at once would run the brief twice (and mark mail read twice), so
# the second one leaves the first to finish. lockf -t 0 fails immediately with
# EX_TEMPFAIL (75) if the lock is held; that's not an error, so exit 0.
if [ "${MORNING_CHAIN_LOCKED:-0}" != "1" ] && [ "$DRY" = "0" ]; then
    MORNING_CHAIN_LOCKED=1 /usr/bin/lockf -s -t 0 "$LOCK" /bin/bash "$SELF" "$@"
    rc=$?
    if [ $rc -eq 75 ]; then
        echo "[chain] another morning chain is already running — leaving it to finish"
        exit 0
    fi
    exit $rc
fi

# The outer launcher runs this chain with RR_FORCE=1 / RR_ATTEMPTS=1 (see the
# plist). Neither may leak into the steps: RR_FORCE would defeat every step's
# "already succeeded today" no-op, and RR_ATTEMPTS=1 would strip their retries.
unset RR_FORCE RR_ATTEMPTS

say() { echo "[chain $(date '+%F %T')] $*"; }
stamp_today() { [ -f "$HOME/Library/Logs/.rr_ok_$1" ] && [ "$(date -r "$HOME/Library/Logs/.rr_ok_$1" +%F)" = "$(date +%F)" ]; }

# step <label> <target> <stale-days> [args...] → the step's exit code.
# Same argument order as resilient_run.sh itself: label, TARGET, stale-days.
# (The first version passed stale-days before the target, so resilient_run
# went looking for a script called "2" — and only the steps that no-op'd on
# their stamp, before touching the target, looked like they worked.)
step() {
    local label="$1"; shift
    local target="$1"
    # Validate the wiring even when the step would be skipped — a skipped step
    # never touches its target, which is exactly how a wrong path hides.
    if [ ! -f "$target" ]; then
        say "BAD STEP $label — target '$target' is not a file"
        return 2
    fi
    if [ "$DRY" = "1" ]; then
        if stamp_today "$label" && ! { [ "$label" = "daily-brief" ] && [ "$FORCE_BRIEF" = "1" ]; }; then
            say "would SKIP $label — already succeeded today"
        else
            say "would RUN  $label"
        fi
        return 0
    fi
    say "→ $label"
    if [ "$label" = "daily-brief" ] && [ "$FORCE_BRIEF" = "1" ]; then
        RR_FORCE=1 /bin/bash "$RR" "$label" "$@"
    else
        /bin/bash "$RR" "$label" "$@"
    fi
    local rc=$?
    say "← $label rc=$rc"
    return $rc
}

note_state() {
    if [ -f "$VAULT_TODAY" ]; then
        local d; d=$(grep -m1 '^date_sort:' "$VAULT_TODAY" | grep -oE '[0-9]{2}-[0-9]{2}')
        if [ "$d" = "$(date +%m-%d)" ]; then echo "today"; else echo "stale ($d)"; fi
    else
        echo "absent"
    fi
}

say "start — Today.md is $(note_state)"
failed=0

# 1 + 2. parse → generate
spine_ok=1
step daily-parse "$SEANIPEDIA/daily.py" 2 --parse || { failed=1; spine_ok=0; say "parse failed — not generating, not briefing"; }
parse_ok=$spine_ok
if [ $spine_ok -eq 1 ]; then
    step daily-generate "$SEANIPEDIA/daily.py" 2 --generate || { failed=1; spine_ok=0; say "generate failed — not briefing"; }
fi

# 3. jobs — independent of the spine (it scrapes and updates the Sheet even with
#    no note to inject into), so attempt it regardless.
step natsec-jobs "$AI_CODE/natsec_jobs/run_daily.sh" 2 || failed=1

# 4. brief — needs today's note. Re-check rather than trust the exit codes: a
#    note that's somehow missing would leave the brief only in output/.
if [ $spine_ok -eq 1 ]; then
    if [ "$DRY" = "1" ] || [ "$(note_state)" = "today" ]; then
        step daily-brief "$AI_CODE/daily_brief/run_daily_brief.sh" 2 || failed=1
    else
        failed=1; say "Today.md is $(note_state) after generate — not briefing"
    fi
fi

# 5. weekly — only after parse, so Saturday's note is filed before Sunday's report.
if [ $parse_ok -eq 1 ]; then
    step weekly-report "$SEANIPEDIA/run_weekly.sh" 8 "$SEANIPEDIA" || failed=1
else
    say "skipping weekly — yesterday's note isn't filed yet"
fi

# 6. reindex — cheap and local; always attempt.
step quotes-index "$SEANIPEDIA/quotes_index.py" 8 \
    && step quotes-blockids "$SEANIPEDIA/quotes_blockids.py" 8 --apply \
    || failed=1

say "end — Today.md is $(note_state); $( [ $failed -eq 0 ] && echo 'all steps ok' || echo 'something failed (see above); the next trigger retries unfinished steps')"
exit $failed
