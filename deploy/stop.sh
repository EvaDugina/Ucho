#!/usr/bin/env bash
set -Eeuo pipefail

BASE_DIR="${BASE_DIR:-/srv/psycho}"
APP_DIR="${APP_DIR:-$BASE_DIR/app}"

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

[ -f "$APP_DIR/docker-compose.yml" ] || die "docker-compose.yml not found at $APP_DIR"

cd "$APP_DIR"
log "Stopping bot container"
docker compose stop bot
docker compose ps bot
