# Paperless Invoice Approval

Interní systém pro vytěžení, kontrolu, rozúčtování, paralelní schválení a manuální export přijatých faktur z Paperless do POHODY. Kompletní testovací stack obsahuje vlastní izolovaný Paperless-ngx; pozdější produkční architektura může použít existující externí Paperless.

## Nejdůležitější invarianty

- Originální PDF zůstává v Paperless; aplikace ukládá jeho ID a načítá jej přes API.
- Správce fronty musí před schvalováním explicitně potvrdit kontrolu originálu.
- Schvaluje se konkrétní rozúčtovaná část za konkrétní středisko a konkrétní revizi.
- Významná změna invaliduje všechna dosavadní schválení aktuální faktury.
- Jediné `REJECT` zamítne celou fakturu; `RETURN` ji vrátí správci.
- POHODA XML generuje deterministický kód a před exportem jej validuje XSD.
- Import do POHODY je výhradně ruční. Až správce explicitně potvrdí import, vznikne stav `IMPORTED_TO_POHODA`.
- Audit je append-only. Secrets, skutečné faktury ani modely se necommitují.
- Workflow, dispozice (`ACTIVE`/ignorováno) a dostupnost Paperless zdroje (`AVAILABLE`/`MISSING`) jsou tři nezávislé osy. Ignorování ani ztráta zdroje nemažou historii.

## Rychlý start

1. Zkopírujte `.env.example` na `.env` a doplňte všechny hodnoty `change-me`.
2. Spusťte `docker-compose config` a zkontrolujte výslednou konfiguraci.
3. Postupujte po etapách podle `docs/DEPLOYMENT.md`. Výchozí Compose v Etapě D spouští také CPU-only Ollamu a jednorázově stáhne nakonfigurovaný model.
4. Na testovací VM otevřete Approval na `http://172.30.172.167/`, Paperless na `http://172.30.172.167:8000/` a Keycloak na `http://172.30.172.167:8081/`.

Etapy B/C synchronizují metadata, tagy a OCR text z Paperless REST API do samostatné approval databáze. Originální PDF se do ní neukládá a UI jej načítá přes autorizovanou backend proxy. Etapa D posílá OCR do lokální Ollamy, přijímá pouze striktní `invoice-extraction.v3`, ukládá každý běh append-only a výsledek ověřuje deterministicky. AI technický stav nikdy nenahrazuje obchodní workflow stav.

Etapa E přidává konfigurovatelná střediska, Decimal rozúčtování, povinnou kontrolu originálu a paralelní assignmenty navázané na konkrétní revizi, středisko a částku. `RETURN` vrací celou fakturu správci, `REJECT` ji globálně zamítá a `REOPEN` vytváří novou auditovanou revizi. Významná změna dat, allocations nebo approverů invaliduje všechna dřívější rozhodnutí bez mazání historie. Podrobný kontrakt je v [docs/APPROVAL_WORKFLOW.md](docs/APPROVAL_WORKFLOW.md).

Etapa F vytváří deterministickým Python generátorem `receivedInvoice`, validuje jej proti verzovanému oficiálnímu POHODA XML 2.x bundle a ukládá immutable exportní snapshot s hashy XML i původního PDF. Do položek se exportují allocations a `pohoda_code`, nikdy approval assignmenty. Správce může stáhnout XML, PDF nebo dávkový ZIP, diagnosticky načíst POHODA response a teprve po skutečném ručním importu explicitně potvrdit `IMPORTED_TO_POHODA`. Viz [docs/POHODA_EXPORT.md](docs/POHODA_EXPORT.md), [docs/POHODA_MAPPING.md](docs/POHODA_MAPPING.md) a [docs/POHODA_XSD.md](docs/POHODA_XSD.md).

Opravná iterace po Etapě F normalizuje český účet deterministicky na `bank_account_raw`, `bank_account_prefix`, `bank_account_number`, `bank_code` a zpětně kompatibilní `bank_account`. Schéma v3 odděluje dodavatelský `supplier_address_raw` na street/city/zip pouze z dodavatelského bloku a eviduje samostatný `ROUNDING` VAT řádek. Matematické reconciliation rozdíly jsou review WARNING; neblokují kontrolu, schválení ani export. Správce má zvláštní pohledy aktivních, ignorovaných a zdrojově chybějících dokladů. Frontend používá skutečné SPA URL včetně přímého `/invoices/{id}`.

Výchozí model je konfigurovatelný přes `OLLAMA_MODEL` (`qwen3:8b`), inference běží s teplotou 0, jedním paralelním požadavkem, kontextem 4096 a `num_gpu=0`. Podrobnosti a bezpečnostní hranice jsou v [docs/AI_EXTRACTION.md](docs/AI_EXTRACTION.md).

Nový POHODA export vyžaduje serverovou hodnotu `POHODA_TARGET_ICO`. Jde o IČO cílové účetní jednotky v POHODĚ, nikoli dodavatele faktury. `POHODA_TARGET_KEY` je volitelný a při prázdné hodnotě se do XML vůbec nezapisuje. UI ukazuje cílové IČO před generováním i u vytvořeného artefaktu.

Dashboard a detail se průběžně obnovují pollingem. Mutace používají očekávané číslo revize; konflikt vrací HTTP 409 a rozepsaná data v prohlížeči zůstávají zachována.

Datumy zůstávají v databázi, API a POHODA XML ve formátu ISO. Uživatelské UI je jednotně zobrazuje a přijímá jako `DD.MM.YYYY`; neexistující datum se odmítne inline. Extrakce rozlišuje Datum vystavení, explicitní české varianty DUZP a Datum splatnosti včetně samostatné provenance a chybějící DUZP neodhaduje.

Testovací Paperless používá vlastní PostgreSQL databázi, Redis a persistentní volumes a nesmí sdílet produkční data ani tokeny. Postup je v [docs/PAPERLESS_INTEGRATION.md](docs/PAPERLESS_INTEGRATION.md) a OIDC v [docs/PAPERLESS_KEYCLOAK.md](docs/PAPERLESS_KEYCLOAK.md).

## Vývoj

Backend:

```text
cd backend
python -m pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload
```

Frontend:

```text
cd frontend
npm ci
npm run test
npm run build
npm run lint
```

Kompletní architektura, nasazení a testovací scénáře jsou v adresáři `docs/`.
