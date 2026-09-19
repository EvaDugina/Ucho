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

}

compose_cmd() {
  local files=(-f docker-compose.yml)
  if proxy_uses_loopback; then
    [ -f "$APP_DIR/docker-compose.proxy.yml" ] || die "docker-compose.proxy.yml not found at $APP_DIR"
    files+=(-f docker-compose.proxy.yml)
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
