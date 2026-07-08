#!/bin/bash
# Nightly auto-commit & push for the knowledge-pipeline repos.
# Commits any pending tracked changes (gitignore protects secrets/runtime state)
# and pushes. Untracked files are intentionally NOT added — new files must be
# committed deliberately so secrets never slip in via a blanket add.
#
# This is personal automation for one specific multi-repo workspace layout
# (this repo plus several sibling project repos checked out under the same
# parent folder) — not a general template. If you're adapting this for your
# own use, edit REPOS below to match whatever sibling repos you actually have.

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPOS=("seanipedia" "daily_brief" "natsec_jobs" "perst" "imessage_fact_checker")
STAMP="$(date '+%Y-%m-%d %H:%M')"

# Wait for the network before `git push`. This fires at 23:30, or on wake if that
# was missed, when DNS may not be ready. A failed push is non-destructive (the
# commit is already local and pushes next night), but waiting lets it succeed
# first try. Mirror the DNS-wait used by the other wrappers; proceed anyway after
# the timeout so local commits still happen even if the network never comes up.
echo "[$STAMP] Waiting 20s for DNS to stabilise after network reconnect..."
sleep 20
for i in $(seq 1 60); do
    if host github.com >/dev/null 2>&1; then
        echo "[$STAMP] DNS ready after $((20 + i * 5))s."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "[$STAMP] WARN: DNS unavailable after 320s — committing anyway; push may fail and retry tomorrow."
        break
    fi
    sleep 5
done

for repo in "${REPOS[@]}"; do
  dir="$BASE/$repo"
  cd "$dir" || continue
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git add -u
    git commit -m "Nightly auto-commit $STAMP" >/dev/null 2>&1
    git push >/dev/null 2>&1 && echo "[$STAMP] $repo: pushed" || echo "[$STAMP] $repo: push FAILED"
  else
    echo "[$STAMP] $repo: nothing to commit"
  fi
done
