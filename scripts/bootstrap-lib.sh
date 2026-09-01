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
  local legacy_version plugin_version
  legacy_version=""
  plugin_version=""
  if command -v docker-compose >/dev/null 2>&1; then
    legacy_version="$(docker-compose version --short 2>/dev/null || true)"
  fi
  plugin_version="$(docker compose version --short 2>/dev/null || true)"
  if [[ "$legacy_version" =~ ^v?2\. ]]; then
    COMPOSE_CMD=(docker-compose)
    log_ok "Docker Compose $legacy_version (docker-compose)"
  elif [[ "$plugin_version" =~ ^v?2\. ]]; then
    COMPOSE_CMD=(docker compose)
    log_ok "Docker Compose $plugin_version (docker compose)"
  else
    die "Docker Compose v2 is required. Install the Docker Compose plugin or v2 standalone binary."
  fi
}

compose() {
  ENV_FILE="$BOOTSTRAP_ENV_FILE" "${COMPOSE_CMD[@]}" --env-file "$BOOTSTRAP_ENV_FILE" "$@"
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
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=reverse-proxy" \
    --format '{{.Ports}}' 2>/dev/null \
    | grep -Eq "(^|, |:)$port->|0\.0\.0\.0:$port->|\[::\]:$port->"
}

port_check() {
  local project port key
  project="$(env_get COMPOSE_PROJECT_NAME)"
  for key in APPROVAL_HTTP_PORT PAPERLESS_HTTP_PORT KEYCLOAK_HTTP_PORT; do
    port="$(env_get "$key")"
    if ss -ltnH "sport = :$port" 2>/dev/null | grep -q .; then
      if port_owned_by_project "$port" "$project"; then
        log_ok "Port $port is owned by this Compose project"
      else
        die "Port $port ($key) is already in use by another process or Compose project. Choose a free port in .env."
      fi
    else
      log_ok "Port $port is available"
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
        short_logs "$service"
        die "$service failed health checks."
      fi
    fi
    sleep 3
  done
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
  compose up -d postgres redis keycloak paperless ollama
  for service in postgres redis keycloak paperless ollama; do
    wait_for_service "$service"
  done

  log_info "Recreating idempotent Keycloak and Ollama jobs on current project networks"
  compose up -d --force-recreate --no-deps keycloak-provision ollama-pull
  for service in keycloak-provision ollama-pull; do
    wait_for_job "$service"
  done

  log_info "Recreating idempotent Paperless provisioning after Keycloak provisioning"
  compose up -d --force-recreate --no-deps paperless-bootstrap
  wait_for_job paperless-bootstrap

  log_info "Starting Approval application services and reverse proxy"
  compose up -d backend worker frontend reverse-proxy
  for service in backend worker frontend reverse-proxy; do
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
