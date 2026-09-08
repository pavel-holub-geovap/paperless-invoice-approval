#!/usr/bin/env bash
# Read-only targeted test of Compose detection and the fully rendered port model.

set -Eeuo pipefail
set +x

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bootstrap-lib.sh
source "$SCRIPT_DIR/bootstrap-lib.sh"

require_command docker
require_command python3
detect_compose

TEST_PROJECT="paperless-invoice-config-test"
APP_TEST_PORT=28080
PAPERLESS_TEST_PORT=28000
KEYCLOAK_TEST_PORT=28081
TEST_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/paperless-compose-config.XXXXXX")"
trap 'rm -rf -- "$TEST_DIRECTORY"' EXIT

BOOTSTRAP_ENV_FILE="$TEST_DIRECTORY/.env"
python3 "$SCRIPT_DIR/bootstrap_support.py" generate-env \
  "$BOOTSTRAP_ROOT/.env.example" "$BOOTSTRAP_ENV_FILE" "10.101.3.85" \
  --project-name "$TEST_PROJECT" \
  --app-host-port "$APP_TEST_PORT" \
  --paperless-host-port "$PAPERLESS_TEST_PORT" \
  --keycloak-host-port "$KEYCLOAK_TEST_PORT"
python3 "$SCRIPT_DIR/bootstrap_support.py" validate-env "$BOOTSTRAP_ENV_FILE"
[[ "$(env_get APP_BASE_URL)" == "http://10.101.3.85:${APP_TEST_PORT}" ]] || die "Approval public URL was not generated from the requested host"
[[ "$(env_get PAPERLESS_PUBLIC_URL)" == "http://10.101.3.85:${PAPERLESS_TEST_PORT}" ]] || die "Paperless public URL was not generated from the requested host"
[[ "$(env_get KEYCLOAK_PUBLIC_URL)" == "http://10.101.3.85:${KEYCLOAK_TEST_PORT}" ]] || die "Keycloak public URL was not generated from the requested host"
[[ "$(env_get KEYCLOAK_BASE_URL)" == "http://keycloak:8080" ]] || die "Keycloak internal URL must keep Compose DNS"
[[ "$(env_get PAPERLESS_BASE_URL)" == "http://paperless:8000" ]] || die "Paperless internal URL must keep Compose DNS"
[[ "$(env_get OLLAMA_BASE_URL)" == "http://ollama:11434" ]] || die "Ollama internal URL must keep Compose DNS"

MODEL="$TEST_DIRECTORY/compose.json"
(cd "$BOOTSTRAP_ROOT" && compose config --format json > "$MODEL")
python3 "$SCRIPT_DIR/bootstrap_support.py" validate-compose-ports \
  "$MODEL" "$TEST_PROJECT" "$APP_TEST_PORT" "$PAPERLESS_TEST_PORT" "$KEYCLOAK_TEST_PORT"
log_ok "Deployment config test PASS (no Docker objects were created)"
