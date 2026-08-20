# Nasazení testovacího stacku

Požadavky: Linux VM, Docker Engine, `docker-compose`, alespoň 8 GB RAM a dostupný externí Paperless. GPU není nutné.

1. Zkopírujte `.env.example` na `.env` a vytvořte silné náhodné hodnoty.
2. Ověřte DNS/HTTPS veřejných URL Keycloak, aplikace a Paperless.
3. Spusťte `docker-compose config` a zkontrolujte, že žádný placeholder nezůstal.
4. Spusťte `docker-compose up -d --build`.
5. Ověřte healthchecky backendu, workeru, PostgreSQL, Keycloak a Ollama.
6. Stáhněte nakonfigurovaný Ollama model do persistentního volume.
7. Proveďte seed testovacích středisek a přihlaste se uživateli provisionovanými z environment secrets.

Zálohujte PostgreSQL a exportní volume. Aktualizaci provádějte přes image tagy a databázovou migraci `alembic upgrade head`. Rollback aplikace nikdy nesmí mazat novější data. Paperless a POHODA nejsou součástí stacku.

