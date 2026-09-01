#!/usr/bin/env bash
# Idempotent bootstrap for a complete isolated Linux test stack.
# No destructive Docker or Git operation is performed.

set -Eeuo pipefail
set +x

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bootstrap-lib.sh
source "$SCRIPT_DIR/bootstrap-lib.sh"

MODE="bootstrap"
FULL_SMOKE=false
for argument in "$@"; do
  case "$argument" in
    --check) MODE="check" ;;
    --status) MODE="status" ;;
    --full-smoke) FULL_SMOKE=true ;;
    -h|--help)
      cat <<'EOF'
Usage:
  ./scripts/bootstrap-test.sh
  ./scripts/bootstrap-test.sh --check
  ./scripts/bootstrap-test.sh --status
  ./scripts/bootstrap-test.sh --full-smoke

ENV_FILE may point to an alternative untracked env file. The script never runs
git pull, never deletes volumes, and is safe to rerun after a partial failure.
EOF
      exit 0
      ;;
    *) die "Unknown argument: $argument" ;;
  esac
done

if [[ "$MODE" == "status" ]]; then
  exec "$SCRIPT_DIR/status.sh"
fi

trap 'printf "[ERROR] Bootstrap stopped at line %s. Persistent data was not deleted; fix the reported cause and rerun the same command.\n" "$LINENO" >&2' ERR

cd "$BOOTSTRAP_ROOT"
log_info "Paperless Invoice Approval test bootstrap"
log_info "Environment file: $BOOTSTRAP_ENV_FILE (values and secrets are not printed)"
preflight

if [[ "$MODE" == "check" ]]; then
  log_ok "CHECK-ONLY PASS: no project state was changed"
  exit 0
fi

if model_ready; then
  log_ok "$(env_get OLLAMA_MODEL) is already present; pull job will only verify the manifest"
else
  log_warn "$(env_get OLLAMA_MODEL) is not present in this project cache. The first pull needs several GB of disk and may take a long time."
fi

log_info "Building project images"
compose build

log_info "Starting or reconciling the Compose stack without deleting data"
if ! reconcile_stack; then
  compose ps -a || true
  die "Compose reconciliation failed. Inspect the short service logs, correct the cause and rerun bootstrap."
fi

log_info "Applying all repository Alembic migrations"
compose exec -T backend alembic upgrade head
alembic_check

model_ready || die "Ollama is healthy but the configured model $(env_get OLLAMA_MODEL) is unavailable."
log_ok "Ollama model $(env_get OLLAMA_MODEL) ready"

runtime_smoke

if [[ "$FULL_SMOKE" == true ]]; then
  log_warn "Full smoke performs real synthetic PDF uploads, OCR and one CPU qwen3:8b inference. It may take many minutes."
  export APP_BASE_URL="$(env_get APP_BASE_URL)"
  export TEST_QUEUE_MANAGER_PASSWORD="$(env_get TEST_QUEUE_MANAGER_PASSWORD)"
  export TEST_APPROVER_1_PASSWORD="$(env_get TEST_APPROVER_1_PASSWORD)"
  compose run --rm --no-deps \
    -e APP_BASE_URL -e TEST_QUEUE_MANAGER_PASSWORD -e TEST_APPROVER_1_PASSWORD \
    -v "$BOOTSTRAP_ROOT/scripts:/smoke:ro" \
    -v "$BOOTSTRAP_ROOT/fixtures:/fixtures:ro" \
    worker python /smoke/smoke_bootstrap_full.py
  unset APP_BASE_URL TEST_QUEUE_MANAGER_PASSWORD TEST_APPROVER_1_PASSWORD
  log_ok "Full synthetic OCR/AI and ISDOC smoke"
fi

log_ok "BOOTSTRAP PASS"
log_info "Approval: $(env_get APP_BASE_URL)"
log_info "Paperless: $(env_get PAPERLESS_PUBLIC_URL)"
log_info "Keycloak: $(env_get KEYCLOAK_PUBLIC_URL)"
log_info "Run ./scripts/status.sh for a read-only status report."
