# Nasazení kompletního testovacího stacku

Testovací checkout patří do `/home/codex/paperless-invoice-approval` na VM `ssh ubuntudocker`. Přenos zdrojového kódu probíhá pouze přes správný GitHub remote; nekopírujte pracovní strom na server bokem.

## Předpoklady

1. Lokální i serverový checkout mají správný remote a čistou větev `main`.
2. Serverová `.env` vznikla z `.env.example`; všechny hodnoty `change-me` byly nahrazeny nezávislými náhodnými secrets.
3. `APP_BASE_URL`, `PAPERLESS_PUBLIC_URL` a `KEYCLOAK_PUBLIC_URL` odpovídají skutečné IP/hostname VM.
4. `free -h` potvrzuje přibližně 8 GiB RAM a 4 GiB swap; průběžně se sleduje `docker stats`.
5. Neexistuje žádné napojení na produkční Paperless ani POHODU.

Před každou etapou spusťte `docker compose config --quiet`. Nikdy nepoužívejte `down -v`, prune příkazy ani nemažte databáze/storage bez výslovného souhlasu.

## Etapa A: infrastruktura, identita a Paperless

```text
docker compose up -d --build postgres redis keycloak keycloak-provision paperless paperless-bootstrap reverse-proxy
docker compose ps
```

Ověřte health PostgreSQL, Redis, Keycloak, Paperless a Nginx. `keycloak-provision` a `paperless-bootstrap` mají skončit kódem 0; jde o idempotentní jednorázové úlohy. Otevřete:

- Paperless: `http://172.30.172.167:8000/`
- Keycloak: `http://172.30.172.167:8081/`

Ověřte přihlášení queue managera přes Keycloak, upload syntetické fixture, dokončení OCR, OCR text a API. Běžný Paperless login zatím nevypínejte.

## Etapa B: Approval aplikace

```text
docker compose up -d --build backend frontend worker
docker compose ps
```

Backend při startu spustí `alembic upgrade head` a seed středisek. Ověřte `http://172.30.172.167/api/health`, UI a přihlášení všech rolí přes Keycloak.

## Etapa C: Paperless REST API → Approval

Nahrajte pouze syntetickou fakturu, nastavte tag `Přijatá faktura` a ověřte, že worker přes REST API vytvoří právě jednu fakturu s autoritativním `paperless_document_id`. Ověřte snapshot metadat/OCR, audit, originální PDF v Approval UI a přechod `NEW → VALIDATION → QUEUE_REVIEW`. V této etapě nevzniká LLM job.

## Etapa D: Ollama

```text
git pull --ff-only
docker compose config
docker compose build
docker compose up -d
docker compose ps
```

Model určuje `OLLAMA_MODEL`; výchozí je `qwen3:4b`. `ollama-pull` ho stáhne do `ollama_data` a worker začne až po úspěšném dokončení. Konfigurace vynucuje jednu paralelní CPU inferenci (`OLLAMA_NUM_GPU=0`), teplotu 0, kontext 4096 a timeout 300 s. Před stažením modelu, po něm a během první inference zaznamenejte `free -h` a `docker stats`. Potom spusťte Stage D smoke test z `docs/TESTING.md` a ověřte golden accuracy i prompt injection.

## Etapy E–F: workflow a export

Projděte paralelní approvals, RETURN, REJECT a invalidaci revize. Nakonec ověřte APPROVED → POHODA XML → XSD → PDF + XML ZIP → `EXPORT_CREATED`. POHODA import zůstává ruční a `IMPORTED_TO_POHODA` vyžaduje explicitní potvrzení.

## Persistence a diagnostika

Persistentní volumes: PostgreSQL, Redis, Paperless data/media/consume/export, runtime API token, Ollama modely a approval export. Restart kontejneru je nesmí odstranit.

Při chybě nejdřív použijte `docker compose ps` a `docker compose logs --tail=200 <service>`. Změnu zdrojů proveďte lokálně, otestujte, commitněte, pushněte a na VM použijte `git pull --ff-only`.
