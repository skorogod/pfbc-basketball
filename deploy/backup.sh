#!/bin/sh
# Ежедневный pg_dump в /backups с ротацией. Копию вне VPS (S3 и т.п.) настраивайте отдельно,
# например rclone по cron на хосте: rclone sync ./backups remote:pfbc-backups
set -eu

RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

while true; do
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    file="/backups/pfbc_${stamp}.dump"
    if pg_dump --format=custom --file="${file}.tmp"; then
        mv "${file}.tmp" "${file}"
        echo "backup ok: ${file}"
    else
        rm -f "${file}.tmp"
        echo "backup FAILED" >&2
    fi
    find /backups -name 'pfbc_*.dump' -mtime "+${RETENTION_DAYS}" -delete
    sleep 86400
done
