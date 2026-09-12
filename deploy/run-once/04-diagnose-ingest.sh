#!/usr/bin/env bash
# Snapshot of what the statewide ingest is doing (host + containers + DB).
set -uo pipefail
cd /opt/nj-hin
ENV=deploy/.env; PGUSER=$(grep ^POSTGRES_USER $ENV | cut -d= -f2); PGDB=$(grep ^POSTGRES_DB $ENV | cut -d= -f2)
echo "== $(date -Is) host"; uptime; free -m | head -2
echo "== host processes"; pgrep -af "ingest_all_real_data|compose exec" || echo "(none)"
echo "== inside api container"; $COMPOSE exec -T api sh -c 'ps -eo pid,etime,rss,args | grep -v "ps -eo" ; echo; du -sh /srv/data/raw/* /srv/data/processed 2>/dev/null; echo; ls -la /srv/data/raw/roads 2>/dev/null | wc -l; ls -t /srv/data/raw/roads 2>/dev/null | head -3'
echo "== db"; $COMPOSE exec -T db psql -U "$PGUSER" -d "$PGDB" -c "select relname, n_live_tup from pg_stat_user_tables order by n_live_tup desc limit 12;" -c "select pid, now()-query_start as age, state, left(query,160) from pg_stat_activity where state<>'idle' and query not ilike '%pg_stat_activity%';"
echo "== api log tail"; $COMPOSE logs --tail=15 --no-color api 2>&1 | tail -15
exit 0
