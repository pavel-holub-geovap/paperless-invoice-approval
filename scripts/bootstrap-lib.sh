#!/usr/bin/env bash
# Shared non-destructive helpers for bootstrap-test.sh and status.sh.

set -Eeuo pipefail
set +x

BOOTSTRAP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP_ENV_FILE="${ENV_FILE:-$BOOTSTRAP_ROOT/.env}"
BOOTSTRAP_TIMEOUT_SECONDS="${BOOTSTRAP_TIMEOUT_SECONDS:-1800}"
COMPOSE_CMD=()

log_info() { printf '[INFO] %s\n' "$*"; }
log_ok() { printf '[OK] %s\n' "$*"; }
log_warn() { printf '[WARN] %s\n' "$*"; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required. Install it and run the command again."
}

env_get() {
  python3 "$BOOTSTRAP_ROOT/scripts/bootstrap_support.py" get "$BOOTSTRAP_ENV_FILE" "$1"
}

detect_compose() {
  local legacy_version plugin_version selected
  legacy_version=""
  plugin_version=""
  plugin_version="$(docker compose version --short 2>/dev/null || true)"
  if [[ -z "$plugin_version" ]]; then
    plugin_version="$(docker compose version 2>/dev/null || true)"
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    legacy_version="$(docker-compose version --short 2>/dev/null || docker-compose version 2>/dev/null || true)"
  fi
  if ! selected="$(python3 "$BOOTSTRAP_ROOT/scripts/bootstrap_support.py" select-compose "$plugin_version" "$legacy_version")"; then
    die "Install the Docker Compose plugin (preferred) or a standalone Compose version >= 2."
  fi
  if [[ "$selected" == "docker compose" ]]; then
    COMPOSE_CMD=(docker compose)
    log_ok "Docker Compose: docker compose $plugin_version"
  elif [[ "$selected" == "docker-compose" ]]; then
    COMPOSE_CMD=(docker-compose)
    log_ok "Docker Compose: docker-compose $legacy_version (standalone fallback)"
  else
    die "Internal Compose detection error: $selected"
  fi
}

compose() {
  APP_HOST_PORT="$(env_get APP_HOST_PORT)" \
  PAPERLESS_HOST_PORT="$(env_get PAPERLESS_HOST_PORT)" \
  KEYCLOAK_HOST_PORT="$(env_get KEYCLOAK_HOST_PORT)" \
  ENV_FILE="$BOOTSTRAP_ENV_FILE" \
    "${COMPOSE_CMD[@]}" --env-file "$BOOTSTRAP_ENV_FILE" "$@"
}

required_files_check() {
  local relative
  local required=(
    docker-compose.yml
    backend/Dockerfile
    frontend/Dockerfile
    infrastructure/nginx/Dockerfile
    infrastructure/postgres/Dockerfile
    backend/alembic.ini
    scripts/paperless_bootstrap.py
    scripts/bootstrap_smoke.py
    schemas/isdoc/6.0.2/isdoc-invoice-6.0.2.xsd
    schemas/isdoc/6.0.2/isdoc-core-6.0.2.xsd
    schemas/pohoda/2025-10-16/data.xsd
  )
  for relative in "${required[@]}"; do
    [[ -f "$BOOTSTRAP_ROOT/$relative" ]] || die "Required repository file is missing: $relative"
  done
  [[ -w "$BOOTSTRAP_ROOT" ]] || die "Repository directory is not writable: $BOOTSTRAP_ROOT"
  log_ok "Required repository files and writable checkout"
}

resource_check() {
  local memory_bytes cpu_count disk_kb disk_gib memory_gib
  memory_bytes="$(awk '/MemTotal:/ {print $2 * 1024}' /proc/meminfo)"
  cpu_count="$(nproc)"
  disk_kb="$(df -Pk "$BOOTSTRAP_ROOT" | awk 'NR==2 {print $4}')"
  memory_gib="$((memory_bytes / 1024 / 1024 / 1024))"
  disk_gib="$((disk_kb / 1024 / 1024))"
  log_info "Host resources: ${cpu_count} CPU, ${memory_gib} GiB RAM, ${disk_gib} GiB free disk"
  (( cpu_count >= 4 )) || die "At least 4 CPU cores are required for the complete test stack."
  (( memory_bytes >= 8 * 1024 * 1024 * 1024 )) || die "At least 8 GiB RAM is required; 12 GiB is recommended for qwen3:8b."
  (( disk_kb >= 15 * 1024 * 1024 )) || die "At least 15 GiB free disk is required; 30 GiB is recommended."
  log_ok "CPU, RAM and disk preflight"
}

port_owned_by_project() {
  local port="$1" project="$2"
  docker ps \
    --filter "publish=$port" \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=reverse-proxy" \
    --format '{{.ID}}' 2>/dev/null \
    | grep -q .
}

port_check() {
  local project port key foreign
  project="$(env_get COMPOSE_PROJECT_NAME)"
  for key in APP_HOST_PORT PAPERLESS_HOST_PORT KEYCLOAK_HOST_PORT; do
    port="$(env_get "$key")"
    foreign="$(docker ps --filter "publish=$port" --format '{{.ID}} {{.Label "com.docker.compose.project"}}' 2>/dev/null | awk -v project="$project" '$2 != project { print }')"
    if [[ -n "$foreign" ]]; then
      die "Host port $port is already in use. Set $key to another free port in .env."
    fi
    if ss -ltnH "sport = :$port" 2>/dev/null | grep -q .; then
      if port_owned_by_project "$port" "$project"; then
        log_ok "$key $port is already owned by this Compose project"
      else
        die "Host port $port is already in use. Set $key to another free port in .env."
      fi
    else
      log_ok "$key $port available"
    fi
  done
}

preflight() {
  [[ "$(uname -s)" == "Linux" ]] || die "bootstrap-test.sh supports Linux only."
  require_command git
  require_command docker
  require_command python3
  require_command awk
  require_command grep
  require_command sed
  require_command df
  require_command nproc
  require_command ss
  [[ -f "$BOOTSTRAP_ENV_FILE" ]] || die "Environment file is missing. Run: cp .env.example .env && nano .env"
  docker info >/dev/null 2>&1 || die "Docker daemon is unavailable or the current user cannot access it. Start Docker and grant normal Docker access without changing socket permissions."
  log_ok "Linux, Git, Docker daemon and current-user Docker access"
  detect_compose
  required_files_check
  python3 "$BOOTSTRAP_ROOT/scripts/bootstrap_support.py" validate-env "$BOOTSTRAP_ENV_FILE"
  resource_check
  port_check
  (cd "$BOOTSTRAP_ROOT" && compose config --quiet) || die "Docker Compose configuration is invalid."
  log_ok "Docker Compose config"
}

container_id_for() {
  (cd "$BOOTSTRAP_ROOT" && compose ps -a -q "$1" 2>/dev/null | head -n 1)
}

short_logs() {
  local service="$1"
  log_warn "Last 80 log lines for $service:"
  (cd "$BOOTSTRAP_ROOT" && compose logs --tail=80 "$service") || true
}

health_history() {
  local service="$1" container
  container="$(container_id_for "$service")"
  [[ -n "$container" ]] || return 0
  log_warn "Last health checks for $service:"
  docker inspect -f '{{range .State.Health.Log}}{{println .End "exit=" .ExitCode .Output}}{{end}}' "$container" 2>/dev/null | tail -n 10 || true
}

service_diagnostics() {
  local service
  for service in "$@"; do
    health_history "$service"
    short_logs "$service"
  done
}

wait_for_service() {
  local service="$1" deadline container status health
  deadline=$((SECONDS + BOOTSTRAP_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    container="$(container_id_for "$service")"
    if [[ -n "$container" ]]; then
      status="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || true)"
      health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || true)"
      if [[ "$status" == "running" && ( "$health" == "healthy" || "$health" == "none" ) ]]; then
        log_ok "$service healthy"
        return 0
      fi
      if [[ "$health" == "unhealthy" || "$status" == "dead" ]]; then
        health_history "$service"
        short_logs "$service"
        die "$service failed health checks."
      fi
    fi
    sleep 3
  done
  health_history "$service"
  short_logs "$service"
  die "Timed out waiting for $service after ${BOOTSTRAP_TIMEOUT_SECONDS}s."
}

wait_for_job() {
  local service="$1" deadline container status exit_code
  deadline=$((SECONDS + BOOTSTRAP_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    container="$(container_id_for "$service")"
    if [[ -n "$container" ]]; then
      status="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || true)"
      exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$container" 2>/dev/null || true)"
      if [[ "$status" == "exited" && "$exit_code" == "0" ]]; then
        log_ok "$service completed successfully"
        return 0
      fi
      if [[ "$status" == "exited" && "$exit_code" != "0" ]]; then
        short_logs "$service"
        die "$service exited with code $exit_code. Fix the reported configuration and rerun bootstrap; volumes are preserved."
      fi
    fi
    sleep 3
  done
  short_logs "$service"
  die "Timed out waiting for one-shot service $service."
}

reconcile_stack() {
  local service
  log_info "Starting core stateful services"
  if ! compose up -d postgres redis keycloak paperless ollama; then
    service_diagnostics postgres redis keycloak paperless ollama
    return 1
  fi
  for service in postgres redis keycloak paperless ollama; do
    wait_for_service "$service"
  done

  log_info "Recreating idempotent Keycloak and Ollama jobs on current project networks"
  if ! compose up -d --force-recreate --no-deps keycloak-provision ollama-pull; then
    service_diagnostics keycloak keycloak-provision ollama ollama-pull
    return 1
  fi
  for service in keycloak-provision ollama-pull; do
    wait_for_job "$service"
  done

  log_info "Recreating idempotent Paperless provisioning after Keycloak provisioning"
  if ! compose up -d --force-recreate --no-deps paperless-bootstrap; then
    service_diagnostics paperless paperless-bootstrap
    return 1
  fi
  wait_for_job paperless-bootstrap

  log_info "Starting Approval application services and reverse proxy"
  if ! compose up -d --no-deps backend reverse-proxy; then
    service_diagnostics backend reverse-proxy
    return 1
  fi
  for service in backend reverse-proxy; do
    wait_for_service "$service"
  done
  if ! compose up -d --no-deps worker frontend; then
    service_diagnostics worker frontend
    return 1
  fi
  for service in worker frontend; do
    wait_for_service "$service"
  done
}

alembic_check() {
  local current heads
  current="$(cd "$BOOTSTRAP_ROOT" && compose exec -T backend alembic current 2>/dev/null)"
  heads="$(cd "$BOOTSTRAP_ROOT" && compose exec -T backend alembic heads 2>/dev/null)"
  python3 - "$current" "$heads" "$BOOTSTRAP_ROOT/scripts/bootstrap_support.py" <<'PY'
import importlib.util
import sys
spec = importlib.util.spec_from_file_location("bootstrap_support", sys.argv[3])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if not module.alembic_revisions_match(sys.argv[1], sys.argv[2]):
    raise SystemExit(f"Alembic mismatch: current={sys.argv[1]!r}, heads={sys.argv[2]!r}")
print(f"[OK] Alembic current matches repository head: {sys.argv[1].strip()}")
PY
}

export_provisioning_verification_env() {
  export KEYCLOAK_ADMIN="$(env_get KEYCLOAK_ADMIN)"
  export KEYCLOAK_ADMIN_PASSWORD="$(env_get KEYCLOAK_ADMIN_PASSWORD)"
  export PAPERLESS_OIDC_CLIENT_ID="$(env_get PAPERLESS_OIDC_CLIENT_ID)"
}

runtime_smoke() {
  export_provisioning_verification_env
  (cd "$BOOTSTRAP_ROOT" && compose exec -T \
    -e KEYCLOAK_ADMIN -e KEYCLOAK_ADMIN_PASSWORD -e PAPERLESS_OIDC_CLIENT_ID \
    backend python /app/ops/bootstrap_smoke.py --provisioning)
  unset KEYCLOAK_ADMIN KEYCLOAK_ADMIN_PASSWORD PAPERLESS_OIDC_CLIENT_ID
}

model_ready() {
  local model output
  model="$(env_get OLLAMA_MODEL)"
  output="$(cd "$BOOTSTRAP_ROOT" && compose exec -T ollama ollama list 2>/dev/null)"
  printf '%s\n' "$output" | awk 'NR > 1 {print $1}' | grep -Fxq "$model"
}
