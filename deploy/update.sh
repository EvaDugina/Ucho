#!/usr/bin/env bash
set -Eeuo pipefail

BASE_DIR="${BASE_DIR:-/srv/psycho}"
APP_DIR="${APP_DIR:-$BASE_DIR/app}"
VAULT_DIR="${VAULT_DIR:-}"
SKIP_TESTS="${SKIP_TESTS:-0}"
PYTHON_BASE_IMAGE="${PYTHON_BASE_IMAGE:-mirror.gcr.io/library/python:3.12-slim}"
export PYTHON_BASE_IMAGE
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/lib.sh"

[ -d "$APP_DIR/.git" ] || die "Bot repo not found at $APP_DIR"
[ -f "$APP_DIR/docker-compose.yml" ] || die "docker-compose.yml not found at $APP_DIR"
[ -f "$APP_DIR/.env" ] || die ".env not found at $APP_DIR"

log "Pulling bot code"
before_head="$(git -C "$APP_DIR" rev-parse HEAD)"
git -C "$APP_DIR" pull --ff-only
after_head="$(git -C "$APP_DIR" rev-parse HEAD)"
if [ "${PSYCHO_UPDATE_REEXECED:-0}" != "1" ] && [ "$before_head" != "$after_head" ]; then
  log "Bot code changed; restarting update script from the new checkout"
  export BASE_DIR APP_DIR VAULT_DIR SKIP_TESTS PYTHON_BASE_IMAGE
  export PSYCHO_UPDATE_REEXECED=1
  exec "$APP_DIR/deploy/update.sh" "$@"
fi

preflight_env
VAULT_DIR="$(env_value "VAULT_HOST_PATH")"

if [ -d "$VAULT_DIR/.git" ]; then
  log "Pulling knowledge vault"
  vault_git fetch --all --prune
  vault_git pull --ff-only
else
  log "Knowledge vault at $VAULT_DIR is not a git checkout; skipping vault pull"
fi

cd "$APP_DIR"

if [ "$SKIP_TESTS" = "1" ]; then
  log "Skipping smoke tests because SKIP_TESTS=1"
else
  log "Running smoke tests in Docker (base image: $PYTHON_BASE_IMAGE)"
  compose_cmd run --rm --build -e VAULT_PATH=/tmp/psycho-test bot pytest tests/smoke
fi

log "Rebuilding and restarting bot (base image: $PYTHON_BASE_IMAGE)"
compose_cmd up -d --build bot
compose_cmd ps bot
compose_cmd logs --tail=80 bot

log "Update done"
