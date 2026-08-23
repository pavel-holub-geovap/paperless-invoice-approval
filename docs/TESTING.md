# Testování

## Lokální kontroly

Backend používá `pytest` a SQLite pro doménové testy; frontend Vitest, Testing Library, TypeScript a Vite. Před commitem spusťte Ruff, backend testy, frontend test/build, XSD kompilaci, syntaktickou Compose validaci a kontrolu secrets.

```text
cd backend
pytest

cd ../frontend
npm run test
npm run build

cd ..
docker compose config --quiet
```

## Povinný Paperless smoke test (Etapa A)

Použijte pouze `fixtures/synthetic/synthetic-invoice-cs-en.pdf`:

1. Přihlaste queue managera do Paperless přes Keycloak.
2. Nahrajte fixture přes UI nebo REST API.
3. Sledujte task do `SUCCESS` a zaznamenejte document ID.
4. Ověřte neprázdný OCR `content`; PDF je image-only a nemá vloženou textovou vrstvu.
5. Nastavte tag `Přijatá faktura` a ověřte jeho načtení přes API.
6. Restartujte Paperless, Redis a PostgreSQL jednotlivě a ověřte, že dokument, OCR i tag přežily. Volumes nemažte.

## Approval integrační test (Etapy B/C)

1. Approval login přes stejnou Keycloak identitu.
2. Worker objeví Paperless dokument právě jednou přes REST API.
3. Approval UI zobrazí originální PDF a uloží `paperless_document_id`.
4. DB snapshot obsahuje název, created timestamp, korespondenta, tagy, OCR text, `SYNCED` a audit objevení/synchronizace/přechodů.
5. Worker přejde bez LLM přes `NEW → VALIDATION → QUEUE_REVIEW` a nastaví Paperless stavový tag.
6. Queue manager vidí dashboard/detail/PDF; approver se přihlásí a vidí sekci „Moje úkoly“, ale nedostane celou frontu.
7. Ověřte `/api/invoices/{id}/pdf` jako PDF a současně absenci PDF bytes v approval databázi.

Na nasazené VM lze skutečný OIDC/API/PDF smoke test spustit bez vypsání hesel:

```text
docker compose run --rm --no-deps \
  -v "$PWD/scripts/smoke_stage_b.py:/smoke_stage_b.py:ro" \
  keycloak-provision python /smoke_stage_b.py
```

Skript načítá testovací credentials pouze z chráněného serverového `.env`, projde Keycloak formulář a callback, ověří role, dashboard, detail, OCR, PDF a oddělení oprávnění approvera. Citlivé hodnoty netiskne.

## Pozdější etapy

Etapa D samostatně ověří Ollama OCR → strict JSON → deterministické validace. Teprve poté se testují approvals, RETURN, REJECT, revize a POHODA export.

Změny tagů jsou povolené pouze v izolované testovací instanci. Reálné faktury, produkční Paperless a POHODA nejsou součástí automatických testů.

## Evidence

Pro každý deployment uložte do reportu commit, `docker compose config --quiet`, `docker compose ps`, healthchecks, relevantní logy bez secrets, RAM před/po OCR a Ollamě, document ID syntetické fixture a výsledek persistence restartu. Modul označte za funkční pouze po praktickém testu.
