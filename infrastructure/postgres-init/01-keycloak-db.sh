#!/bin/sh
set -eu

for required_name in KEYCLOAK_DB_PASSWORD PAPERLESS_DB_PASSWORD; do
  eval "required_value=\${$required_name:-}"
  if [ -z "$required_value" ]; then
    echo "$required_name is required" >&2
    exit 1
  fi
done

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=keycloak_password="$KEYCLOAK_DB_PASSWORD" \
  --set=paperless_password="$PAPERLESS_DB_PASSWORD" <<-EOSQL
SELECT format('CREATE ROLE keycloak LOGIN PASSWORD %L', :'keycloak_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keycloak') \gexec
SELECT 'CREATE DATABASE keycloak OWNER keycloak'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak') \gexec

SELECT format('CREATE ROLE paperless LOGIN PASSWORD %L', :'paperless_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'paperless') \gexec
SELECT 'CREATE DATABASE paperless OWNER paperless'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'paperless') \gexec
EOSQL
