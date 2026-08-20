FROM postgres:17-alpine
COPY infrastructure/postgres-init/01-keycloak-db.sh /docker-entrypoint-initdb.d/01-keycloak-db.sh
RUN chmod 0755 /docker-entrypoint-initdb.d/01-keycloak-db.sh

