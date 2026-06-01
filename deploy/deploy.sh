#!/usr/bin/env bash
set -Eeuo pipefail

BOT_REPO_URL="${BOT_REPO_URL:-https://github.com/EvaDugina/Ucho.git}"
BRANCH="${BRANCH:-main}"
BASE_DIR="${BASE_DIR:-/srv/psycho}"
APP_DIR="${APP_DIR:-$BASE_DIR/app}"
VAULT_DIR="${VAULT_DIR:-$BASE_DIR/vault}"
VAULT_REPO_URL="${VAULT_REPO_URL:-}"
SKIP_TESTS="${SKIP_TESTS:-0}"
PYTHON_BASE_IMAGE="${PYTHON_BASE_IMAGE:-mirror.gcr.io/library/python:3.12-slim}"
export PYTHON_BASE_IMAGE

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

install_system_packages() {
  log "Installing base packages"
  $SUDO apt-get update
  $SUDO apt-get install -y ca-certificates curl git
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker and compose plugin already installed"
    return
  fi

  log "Installing Docker Engine and compose plugin"
  $SUDO install -m 0755 -d /etc/apt/keyrings
  if [ ! -f /etc/apt/keyrings/docker.asc ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $SUDO tee /etc/apt/keyrings/docker.asc >/dev/null
    $SUDO chmod a+r /etc/apt/keyrings/docker.asc
  fi

  . /etc/os-release
  codename="${UBUNTU_CODENAME:-$VERSION_CODENAME}"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
    | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null

  $SUDO apt-get update
  $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  $SUDO systemctl enable --now docker
}

prepare_dirs() {
  log "Preparing directories in $BASE_DIR"
  $SUDO mkdir -p "$BASE_DIR" "$VAULT_DIR"
  if [ -n "$SUDO" ]; then
    $SUDO chown -R "$(id -u):$(id -g)" "$BASE_DIR"
  fi
}

sync_app_repo() {
  log "Syncing bot code: $BOT_REPO_URL"
  if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --all --prune
    git -C "$APP_DIR" checkout "$BRANCH"
    git -C "$APP_DIR" pull --ff-only
    return
  fi

  if [ -e "$APP_DIR" ] && [ "$(find "$APP_DIR" -mindepth 1 -maxdepth 1 | wc -l)" -gt 0 ]; then
    die "$APP_DIR exists and is not an empty git checkout"
  fi

  git clone --branch "$BRANCH" "$BOT_REPO_URL" "$APP_DIR"
}

sync_vault_repo() {
  if [ -n "$VAULT_REPO_URL" ]; then
    log "Syncing knowledge vault: $VAULT_REPO_URL"
    if [ -d "$VAULT_DIR/.git" ]; then
      git -C "$VAULT_DIR" pull --ff-only
    else
      rm -rf "$VAULT_DIR"
      git clone "$VAULT_REPO_URL" "$VAULT_DIR"
    fi
    return
  fi

  if [ -d "$VAULT_DIR/.git" ]; then
    log "Pulling existing knowledge vault"
    git -C "$VAULT_DIR" pull --ff-only || true
  else
    log "Knowledge vault repo URL not set; leaving $VAULT_DIR as a local directory"
  fi
}

set_env_default() {
  local key="$1"
  local value="$2"
  local env_file="$APP_DIR/.env"

  if grep -q "^${key}=" "$env_file"; then
    if grep -q "^${key}=$" "$env_file"; then
      sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
    fi
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$env_file"
  fi
}

env_value() {
  local key="$1"
  grep -E "^${key}=" "$APP_DIR/.env" | tail -n 1 | cut -d= -f2-
}

require_env_value() {
  local key="$1"
  local value
  value="$(env_value "$key" || true)"
  if [ -z "$value" ]; then
    die "Fill $key in $APP_DIR/.env and run this script again"
  fi
}

require_llm_key() {
  local openrouter_key
  local aitunnel_key
  openrouter_key="$(env_value "OPENROUTER_API_KEY" || true)"
  aitunnel_key="$(env_value "AITUNNEL_API_KEY" || true)"
  if [ -z "$openrouter_key" ] && [ -z "$aitunnel_key" ]; then
    die "Fill OPENROUTER_API_KEY (preferred) or AITUNNEL_API_KEY in $APP_DIR/.env and run this script again"
  fi
}

prepare_env() {
  log "Preparing app .env"
  if [ ! -f "$APP_DIR/.env" ] && [ -f "$SCRIPT_DIR/.env" ]; then
    install -m 600 "$SCRIPT_DIR/.env" "$APP_DIR/.env"
  fi

  if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    set_env_default "VAULT_HOST_PATH" "$VAULT_DIR"
    set_env_default "VAULT_PATH" "/vault"
    die "Created $APP_DIR/.env from example. Fill TELEGRAM_BOT_TOKEN, OWNER_TELEGRAM_ID and OPENROUTER_API_KEY (or AITUNNEL_API_KEY), then run again"
  fi

  chmod 600 "$APP_DIR/.env"
  set_env_default "VAULT_HOST_PATH" "$VAULT_DIR"
  set_env_default "VAULT_PATH" "/vault"
  set_env_default "AITUNNEL_BASE_URL" "https://api.aitunnel.ru/v1"

  require_env_value "TELEGRAM_BOT_TOKEN"
  require_env_value "OWNER_TELEGRAM_ID"
  require_llm_key
}

run_checks() {
  if [ "$SKIP_TESTS" = "1" ]; then
    log "Skipping smoke tests because SKIP_TESTS=1"
    return
  fi

  log "Running smoke tests in Docker (base image: $PYTHON_BASE_IMAGE)"
  cd "$APP_DIR"
  docker compose run --rm --build -e VAULT_PATH=/tmp/psycho-test bot pytest tests/smoke
}

start_bot() {
  log "Building and starting bot (base image: $PYTHON_BASE_IMAGE)"
  cd "$APP_DIR"
  docker compose up -d --build bot
  docker compose ps bot
  docker compose logs --tail=80 bot
}

install_system_packages
install_docker
prepare_dirs
sync_app_repo
sync_vault_repo
prepare_env
run_checks
start_bot

log "Done. Bot app: $APP_DIR; vault: $VAULT_DIR"
