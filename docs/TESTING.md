# Testování

## Lokální kontroly

Backend používá `pytest` a SQLite pro doménové testy; frontend Vitest, Testing Library, TypeScript a Vite. Před commitem spusťte Ruff, backend testy, frontend test/build, XSD kompilaci, syntaktickou Compose validaci a kontrolu secrets.

```text
cd backend
pytest

cd ../frontend
npm run test
npm run build
npm run lint

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

## AI integrační test (Etapa D)

Po nasazení počkejte na `ollama-pull` s kódem 0 a healthy worker. Smoke test ověří skutečný tok Paperless OCR → Ollama → strict JSON → Pydantic → validace → DB/API, spustí bezpečnou re-extrakci a samostatnou reálnou prompt-injection inferenci:

```text
docker compose run --rm --no-deps \
  -v "$PWD/scripts:/smoke:ro" \
  -v "$PWD/fixtures/synthetic:/fixtures:ro" \
  -e STAGE_D_GOLDEN=/fixtures/synthetic-invoice-cs-en.expected.json \
  worker python /smoke/smoke_stage_d.py
```

Golden sada má 21 porovnávaných hodnot včetně tří polí DPH řádku. Report uvádí `correct`, `wrong`, `missing`, první/opakovaný čas inference a validační souhrn. Prompt-injection test zvlášť porovná napadený běh s baseline stejného OCR; nesmí změnit pole, na která útok míří, ani obsahovat útočníkův dodavatel nebo XML, a ostatní generativní rozdíly explicitně vypíše. Současně zaznamenejte `free -h` a `docker stats --no-stream` před stažením modelu, po stažení a během inference.

## Schvalovací integrační test (Etapa E)

Po migraci `0004` spusťte skutečný workflow se čtyřmi Keycloak uživateli:

```text
docker compose run --rm --no-deps --env-from-file .env \
  -v "$PWD/scripts:/smoke:ro" \
  worker python /smoke/smoke_stage_e.py
```

Skript opraví platební údaje syntetické faktury podle fixture, používá střediska 200/300 a částky 700/510 Kč, ověří všechny submit preconditions, RETURN, REJECT, REOPEN, invalidaci po změně částky/střediska/approvera/fakturačního údaje, cizí assignment HTTP 403, idempotentní double-click a skutečná souběžná rozhodnutí. Nakonec provede sekvenci tří approvals a čeká na Paperless tag `Schváleno`. Hesla čte pouze z VM `.env` a netiskne je.

## POHODA exportní integrační test (Etapa F)

Po migraci `0006` spusťte skutečný export nad `paperless_document_id=1`:

```text
docker compose run --rm --no-deps --env-from-file .env \
  -v "$PWD/scripts:/smoke:ro" \
  worker python /smoke/smoke_stage_f.py
```

Skript případně doplní přesnou strukturovanou adresu a nechá novou revizi znovu schválit reálnými OIDC uživateli. Ověří dvě allocations 700/510 Kč na střediska 200/300, první XML a auditovaný deterministický re-export, Windows-1250, `receivedInvoice`, adresu s `linkToAddress=false`, XSD-validitu, hash originálního Paperless PDF, stabilní ZIP/batch a diagnostický upload oficiální response fixture. Závěrečný stav je `EXPORT_CREATED`; skript nikdy nepotvrdí `IMPORTED_TO_POHODA`.

Doménová sada navíc testuje 1 středisko/1 sazbu, více středisek/1 sazbu, 1 středisko/více sazeb, explicitní split více středisek/více sazeb a rounding remainder. Negativní XSD testy porušují povinný element, pořadí, datový typ, enum a namespace.

Změny tagů jsou povolené pouze v izolované testovací instanci. Reálné faktury, produkční Paperless a POHODA nejsou součástí automatických testů.

## Opravná iterace po Etapě F

Lokální sada navíc ověřuje varianty českého účtu (s/bez předčíslí, mezery, oddělený a kombinovaný vstup, IBAN-only, nevalidní vstup a LLM duplikaci hodnoty), modulo-11 checksum, POHODA rozdělení `accountNo`/`bankCode`, přesně tři WARNING souhrnných DPH, dispozici/restore, filtry, idempotentní source audit a rozlišení 404 od 5xx. Frontend testuje detail → Fronta, přímý deep link, browser `popstate`, varování `MISSING` a blokované PDF/workflow akce.

Skutečný test vytvoří dvě jednoznačně pojmenované vlastní syntetické položky, čeká na OCR, Approval sync a AI, ověří normalizaci chybného kombinovaného účtu, označí druhou jako duplicitu, ověří Paperless tag a smaže výhradně ID vytvořená vlastním během:

```text
docker compose run --rm --no-deps --env-from-file .env \
  -v "$PWD/scripts:/smoke:ro" \
  -v "$PWD/fixtures/synthetic:/fixtures:ro" \
  worker python /smoke/smoke_correction.py
```

Po smazání musí reconciliation nastavit `MISSING`, vytvořit jednu blocking validation a audity, PDF i nový export musí vrátit HTTP 409. Skript v `finally` maže jen přesná Paperless ID vrácená jeho dvěma upload tasky. Uživatelské dokumenty ani původní syntetická faktura se nemažou. Read-only inventuru bez OCR obsahu lze spustit jako `/smoke/inventory_correction.py`.

## Evidence

Pro každý deployment uložte do reportu commit, `docker compose config --quiet`, `docker compose ps`, healthchecks, relevantní logy bez secrets, RAM před/po OCR a Ollamě, document ID syntetické fixture, migraci, XML/PDF/ZIP hashe a výsledek persistence restartu. Etapa F je technicky připravena po XSD a smoke testu, praktická kompatibilita však vyžaduje ruční import konkrétního XML do testovací POHODY, response XML a kontrolní export.

## Regrese opravné iterace

Automatické testy ověřují rozdílné IČO dodavatele a cílové účetní jednotky, vynechání prázdného `key`, fail-fast bez cílového IČO, normalizaci banky, strukturovaný stale-revision HTTP 409, obohacení auditu korelačním ID a zachování rozepsaného formuláře při polling aktualizaci. Před předáním spusťte celý backend, frontend testy, frontend build a smoke scénáře B–F.

Při reálném 8B testu zaznamenejte velikost modelu, `free -h` před načtením, po načtení a maximum během inference, dobu každé inference a metadata nových extraction běhů. Neprovádějte fallback na 4B.

Nový read-mostly smoke `scripts/smoke_correction_iteration.py` ověřuje obě řazení podle source timestampu, stale HTTP 409, cílovou konfiguraci POHODA, request ID a audit stažení PDF. Spouští se stejně jako ostatní skripty v worker image s read-only mountem adresáře `scripts`.

`scripts/smoke_qwen8_multi.py` spouští nové append-only kandidátní extrakce na třech existujících dostupných fakturách, vyžaduje metadata `model=qwen3:8b`, vypíše účetní pole a ověří, že se business workflow nezměnilo.
