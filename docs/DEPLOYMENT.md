# Nasazení

Autoritativní postup pro čisté kompletní testovací nasazení je v
[DEPLOYMENT_TEST.md](DEPLOYMENT_TEST.md). Nový server se připravuje jediným
idempotentním příkazem `./scripts/bootstrap-test.sh`; skript provede preflight,
build, start, provisioning, migrace a základní runtime smoke bez mazání dat.
Používá přednostně `docker compose` ve všech kompatibilních verzích >= 2; v5 je
platná. Na sdíleném hostu oddělte instanci pomocí `COMPOSE_PROJECT_NAME` a
`APP_HOST_PORT`, `PAPERLESS_HOST_PORT`, `KEYCLOAK_HOST_PORT`.

## Bezpečný upgrade existujícího testovacího stacku

```bash
git fetch origin
git pull --ff-only
./scripts/bootstrap-test.sh --check
./scripts/bootstrap-test.sh
./scripts/status.sh
```

Zdrojový kód se na server přenáší pouze přes ověřený Git remote. Serverový `.env`
je chráněný a necommitovaný. Upgrade nepoužívá `down -v`, prune ani mazání
databází, storage, auditů, dokumentů, artifactů či modelů.

## Co bootstrap zachovává

- PostgreSQL data Approval, Keycloak a Paperless;
- Redis stav a Paperless data/media/consume/export;
- runtime Paperless servisní API token;
- Ollama model cache;
- Approval exporty, immutable artifacty a auditní historii.

Provisioning realm/clients/rolí/uživatelů, Paperless tagů a servisního účtu je
idempotentní. Alembic migrace jsou dopředné a bootstrap vždy ověří, že databáze
odpovídá repository head. Obnova ze zálohy nebo downgrade je samostatná
potenciálně destruktivní operace a není součástí automatického deploymentu.

## Diagnostika

```bash
./scripts/status.sh
docker compose ps -a
docker compose logs --tail=100 <service>
```

Konkrétní inventura integrační VM patří do `DEPLOYMENT_ENVIRONMENT.md`, nikoli do
obecného onboarding návodu. Testovací konfigurace se nesmí spojovat s produkčním
Paperless ani přímo zapisovat do POHODY.
