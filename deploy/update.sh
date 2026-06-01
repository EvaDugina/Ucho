#!/usr/bin/env bash
set -Eeuo pipefail

BASE_DIR="${BASE_DIR:-/srv/psycho}"
APP_DIR="${APP_DIR:-$BASE_DIR/app}"
VAULT_DIR="${VAULT_DIR:-}"
SKIP_TESTS="${SKIP_TESTS:-0}"
PYTHON_BASE_IMAGE="${PYTHON_BASE_IMAGE:-mirror.gcr.io/library/python:3.12-slim}"
export PYTHON_BASE_IMAGE

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

[ -d "$APP_DIR/.git" ] || die "Bot repo not found at $APP_DIR"
[ -f "$APP_DIR/docker-compose.yml" ] || die "docker-compose.yml not found at $APP_DIR"
[ -f "$APP_DIR/.env" ] || die ".env not found at $APP_DIR"

env_value() {
  local key="$1"
  grep -E "^${key}=" "$APP_DIR/.env" | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

if [ -z "$VAULT_DIR" ]; then
  VAULT_DIR="$(env_value "VAULT_HOST_PATH" || true)"
fi
VAULT_DIR="${VAULT_DIR:-$BASE_DIR/vault}"

log "Pulling bot code"
git -C "$APP_DIR" pull --ff-only

if [ -d "$VAULT_DIR/.git" ]; then
  log "Pulling knowledge vault"
  git -C "$VAULT_DIR" fetch --all --prune
  git -C "$VAULT_DIR" pull --ff-only
else
  log "Knowledge vault at $VAULT_DIR is not a git checkout; skipping vault pull"
fi

cd "$APP_DIR"

if [ "$SKIP_TESTS" = "1" ]; then
  log "Skipping smoke tests because SKIP_TESTS=1"
else
  log "Running smoke tests in Docker (base image: $PYTHON_BASE_IMAGE)"
  docker compose run --rm --build -e VAULT_PATH=/tmp/psycho-test bot pytest tests/smoke
fi

log "Rebuilding and restarting bot (base image: $PYTHON_BASE_IMAGE)"
docker compose up -d --build bot
docker compose ps bot
docker compose logs --tail=80 bot

log "Update done"
