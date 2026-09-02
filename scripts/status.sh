#!/usr/bin/env bash
# Read-only health and provisioning report for the test stack.

set -Eeuo pipefail
set +x

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bootstrap-lib.sh
source "$SCRIPT_DIR/bootstrap-lib.sh"

cd "$BOOTSTRAP_ROOT"
require_command git
require_command docker
require_command python3
[[ -f "$BOOTSTRAP_ENV_FILE" ]] || die "Environment file is missing: $BOOTSTRAP_ENV_FILE"
docker info >/dev/null 2>&1 || die "Docker daemon is unavailable or inaccessible."
detect_compose
python3 "$BOOTSTRAP_ROOT/scripts/bootstrap_support.py" validate-env "$BOOTSTRAP_ENV_FILE"
compose config --quiet

log_info "Git HEAD: $(git rev-parse --short=12 HEAD)"
log_info "Compose project: $(env_get COMPOSE_PROJECT_NAME)"
log_info "Host ports: Approval=$(env_get APP_HOST_PORT), Paperless=$(env_get PAPERLESS_HOST_PORT), Keycloak=$(env_get KEYCLOAK_HOST_PORT)"
compose ps -a

failures=0
for service in postgres redis keycloak paperless ollama backend worker frontend reverse-proxy; do
  container="$(container_id_for "$service")"
  if [[ -z "$container" ]]; then
    log_warn "$service is absent"
    failures=$((failures + 1))
    continue
  fi
  status="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || true)"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || true)"
  if [[ "$status" == "running" && ( "$health" == "healthy" || "$health" == "none" ) ]]; then
    log_ok "$service: $status/$health"
  else
    log_warn "$service: $status/$health"
    failures=$((failures + 1))
  fi
done

for service in keycloak-provision paperless-bootstrap ollama-pull; do
  container="$(container_id_for "$service")"
  status="${container:+$(docker inspect -f '{{.State.Status}}/{{.State.ExitCode}}' "$container" 2>/dev/null || true)}"
  if [[ "$status" == "exited/0" ]]; then
    log_ok "$service: $status"
  else
    log_warn "$service: ${status:-absent}"
    failures=$((failures + 1))
  fi
done

if [[ "$failures" -eq 0 ]]; then
  alembic_check
  model_ready || die "Configured Ollama model $(env_get OLLAMA_MODEL) is unavailable."
  compose exec -T backend python /app/ops/bootstrap_smoke.py
  log_ok "STATUS PASS"
else
  die "STATUS FAIL: $failures service or provisioning checks failed. No state was changed."
fi
