#!/usr/bin/env bash
# Runs on the VM from the hin-autoupdate systemd timer (every 3 minutes).
#
#   1. Fast-forwards /opt/nj-hin to origin/main. If HEAD moved (or
#      /var/log/nj-hin/force-update exists) the compose stack is rebuilt, so a
#      push to main is all it takes to deploy - no SSH needed.
#   2. Executes any new script in deploy/run-once/ exactly once (tracked in
#      /var/lib/nj-hin/done). Use this for one-off jobs such as data ingestion.
#   3. Refreshes /var/log/nj-hin/{ps.txt,compose.log} so the stack's state can
#      be read at https://hin-api.mhaske.com/_boot/ without logging in.
set -uo pipefail

REPO=/opt/nj-hin
LOG=/var/log/nj-hin
STATE=/var/lib/nj-hin
mkdir -p "$LOG" "$STATE/done"
cd "$REPO" || exit 1

# The checkout is owned by ubuntu but the timer runs as root.
GIT="git -c safe.directory=$REPO"
export COMPOSE="docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env"

before=$($GIT rev-parse HEAD)
if $GIT fetch -q origin main; then
  $GIT merge -q --ff-only origin/main 2>/dev/null || $GIT reset -q --hard origin/main
fi
after=$($GIT rev-parse HEAD)

if [ "$before" != "$after" ] || [ -f "$LOG/force-update" ]; then
  rm -f "$LOG/force-update"
  {
    echo "== $(date -Is) deploying ${before:0:7} -> ${after:0:7}"
    $COMPOSE up -d --build --remove-orphans
    $COMPOSE image prune -f
  } >> "$LOG/update.log" 2>&1
fi

# One-shot jobs: deploy/run-once/NN-name.sh, run in lexical order, once each.
for f in deploy/run-once/*.sh; do
  [ -e "$f" ] || continue
  key=$(basename "$f")
  [ -e "$STATE/done/$key" ] && continue
  echo "== $(date -Is) run-once $key" >> "$LOG/update.log"
  if bash "$f" >> "$LOG/runonce-$key.log" 2>&1; then
    touch "$STATE/done/$key"
    echo "== $(date -Is) run-once $key OK" >> "$LOG/update.log"
  else
    echo "== $(date -Is) run-once $key FAILED (will retry next tick)" >> "$LOG/update.log"
  fi
done

docker ps -a > "$LOG/ps.txt" 2>&1
$COMPOSE logs --tail=200 --no-color > "$LOG/compose.log" 2>&1
date -Is > "$LOG/last-tick.txt"
