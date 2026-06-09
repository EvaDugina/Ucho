#!/usr/bin/env bash

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

env_file() {
  printf '%s/.env' "$APP_DIR"
}

env_value() {
  local key="$1"
  local file
  file="$(env_file)"
  [ -f "$file" ] || return 0
  grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

require_env_value() {
  local key="$1"
  local value
  value="$(env_value "$key" || true)"
  if [ -z "$value" ]; then
    die "Fill $key in $(env_file) and run this script again"
  fi
}

require_llm_key() {
  local openrouter_key
  local aitunnel_key
  openrouter_key="$(env_value "OPENROUTER_API_KEY" || true)"
  aitunnel_key="$(env_value "AITUNNEL_API_KEY" || true)"
  if [ -z "$openrouter_key" ] && [ -z "$aitunnel_key" ]; then
    die "Fill OPENROUTER_API_KEY (preferred) or AITUNNEL_API_KEY in $(env_file) and run this script again"
  fi
}

preflight_env() {
  local missing=()
  local key
  local value
  for key in TELEGRAM_BOT_TOKEN OWNER_TELEGRAM_ID VAULT_HOST_PATH; do
    value="$(env_value "$key" || true)"
    if [ -z "$value" ]; then
      missing+=("$key")
    fi
  done

  local openrouter_key
  local aitunnel_key
  openrouter_key="$(env_value "OPENROUTER_API_KEY" || true)"
  aitunnel_key="$(env_value "AITUNNEL_API_KEY" || true)"
  if [ -z "$openrouter_key" ] && [ -z "$aitunnel_key" ]; then
    missing+=("OPENROUTER_API_KEY or AITUNNEL_API_KEY")
  fi

  if [ "${#missing[@]}" -gt 0 ]; then
    local joined=""
    for key in "${missing[@]}"; do
      if [ -n "$joined" ]; then
        joined="$joined, "
      fi
      joined="$joined$key"
    done
    die "Fill required variables in $(env_file): $joined"
  fi

  local key_path
  key_path="$(env_value "VAULT_GIT_SSH_KEY_HOST_PATH" || true)"
  if [ -n "$key_path" ] && [ ! -f "$key_path" ]; then
    die "VAULT_GIT_SSH_KEY_HOST_PATH points to missing file: $key_path"
  fi
}

host_git_ssh_command() {
  local key_path
  key_path="$(env_value "VAULT_GIT_SSH_KEY_HOST_PATH" || true)"
  if [ -z "$key_path" ]; then
    return 1
  fi
  printf 'ssh -i %q -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new' "$key_path"
}

host_git() {
  local ssh_command
  if ssh_command="$(host_git_ssh_command)"; then
    GIT_SSH_COMMAND="$ssh_command" git "$@"
  else
    git "$@"
  fi
}

vault_git() {
  host_git -C "$VAULT_DIR" "$@"
}

compose_cmd() {
  local key_path
  local files=(-f docker-compose.yml)
  if proxy_uses_loopback; then
    [ -f "$APP_DIR/docker-compose.proxy.yml" ] || die "docker-compose.proxy.yml not found at $APP_DIR"
    files+=(-f docker-compose.proxy.yml)
  fi

  key_path="$(env_value "VAULT_GIT_SSH_KEY_HOST_PATH" || true)"
  if [ -n "$key_path" ]; then
    [ -f "$APP_DIR/docker-compose.ssh.yml" ] || die "docker-compose.ssh.yml not found at $APP_DIR"
    [ -f "$key_path" ] || die "VAULT_GIT_SSH_KEY_HOST_PATH points to missing file: $key_path"
    files+=(-f docker-compose.ssh.yml)
  fi

  docker compose "${files[@]}" "$@"
}

proxy_uses_loopback() {
  local key
  local value
  for key in TELEGRAM_PROXY_URL HTTP_PROXY HTTPS_PROXY ALL_PROXY; do
    value="$(env_value "$key" || true)"
    case "$value" in
      *://127.* | *://localhost:* | *://[::1]:*)
        return 0
        ;;
    esac
  done
  if [ "${USE_HOST_NETWORK_PROXY:-0}" = "1" ]; then
    return 0
  fi
  return 1
}
