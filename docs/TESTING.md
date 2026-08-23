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

## Approval integrační test (Etapy B–F)

1. Approval login přes stejnou Keycloak identitu.
2. Worker objeví Paperless dokument právě jednou přes REST API.
3. Approval UI zobrazí originální PDF a uloží `paperless_document_id`.
4. Ollama vrátí strict JSON, následují deterministické validace a `QUEUE_REVIEW`.
5. Projděte paralelní approval, RETURN, REJECT, invalidaci revize a znovuschválení.
6. Ověřte XSD-validní POHODA XML, PDF + XML ZIP, `EXPORT_CREATED` a oddělené ruční potvrzení `IMPORTED_TO_POHODA`.

Změny tagů jsou povolené pouze v izolované testovací instanci. Reálné faktury, produkční Paperless a POHODA nejsou součástí automatických testů.

## Evidence

Pro každý deployment uložte do reportu commit, `docker compose config --quiet`, `docker compose ps`, healthchecks, relevantní logy bez secrets, RAM před/po OCR a Ollamě, document ID syntetické fixture a výsledek persistence restartu. Modul označte za funkční pouze po praktickém testu.
