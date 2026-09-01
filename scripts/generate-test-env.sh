#!/usr/bin/env bash
# Generate a secure untracked .env for a new Linux test host.

set -Eeuo pipefail
set +x

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DESTINATION="${ENV_FILE:-$ROOT/.env}"
HOST="${1:-$(hostname -f 2>/dev/null || hostname)}"

[[ "$(uname -s)" == "Linux" ]] || {
  printf '[ERROR] generate-test-env.sh supports Linux only.\n' >&2
  exit 1
}
command -v python3 >/dev/null 2>&1 || {
  printf '[ERROR] python3 is required.\n' >&2
  exit 1
}

python3 "$SCRIPT_DIR/bootstrap_support.py" generate-env \
  "$ROOT/.env.example" "$DESTINATION" "$HOST"
python3 "$SCRIPT_DIR/bootstrap_support.py" validate-env "$DESTINATION"
printf '[OK] Edit public URLs, optional POHODA settings and mail address if needed.\n'
printf '[INFO] Secrets were generated independently and were not printed.\n'
