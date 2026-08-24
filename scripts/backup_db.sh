#!/bin/sh
# Nightly substrate backup — the facts/events history is the moat; losing it is
# a cold start. Cron example:  0 3 * * * /path/to/super-app/scripts/backup_db.sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p backups
STAMP=$(date +%Y-%m-%d_%H%M)
docker compose exec -T db pg_dump -U superapp superapp | gzip > "backups/superapp_${STAMP}.sql.gz"
# Keep the last 30 days.
ls -1t backups/superapp_*.sql.gz | tail -n +31 | xargs rm -f 2>/dev/null || true
echo "wrote backups/superapp_${STAMP}.sql.gz"
