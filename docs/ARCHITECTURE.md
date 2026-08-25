# Architektura

## Komponenty

- `frontend`: statický React build; používá pouze backend API a serverovou OIDC session.
- `backend`: FastAPI, autorizační kontrola, orchestrace use-cases a OpenAPI.
- `worker`: stejný aplikační balík; synchronizuje tagged dokumenty z Paperless REST API, obsluhuje databázovou frontu stavových tagů a sériově provádí AI extrakce.
- `postgres`: oddělené databáze a credentials pro approval aplikaci, Keycloak a Paperless v jednom persistentním clusteru.
- `redis`: persistentní broker/cache pouze pro izolovaný testovací Paperless; approval fronta zůstává v PostgreSQL.
- `paperless`: izolovaná testovací autorita pro originální PDF, OCR, metadata a stavové tagy; runtime integrace probíhá výhradně přes REST API.
- `keycloak`: centrální OIDC identita s importovaným realm konfigurací.
- `ollama`: lokální CPU-only inference; `ollama-pull` idempotentně připraví nakonfigurovaný model v persistentním volume před startem workeru.
- `nginx`: jediná publikovaná vstupní vrstva pro Approval (`:80`), Paperless (`:8000`) a Keycloak (`:8081`).
- Produkční varianta může později nahradit testovací Paperless existující externí instancí bez přímého DB propojení.

## Vrstvy backendu

`api/routes` mapuje HTTP na use-cases; `services` obsahuje workflow, validace, audit, exporty a XML; `integrations` izoluje Paperless/Ollama/OIDC; `models` a `schemas` definují persistence a kontrakty. Route nesmí přímo přepisovat workflow stav.

## POHODA export

Exportní hranice je jednosměrná a offline: aplikace vytváří soubory, ale s POHODOU nekomunikuje. `PohodaInvoiceXmlGenerator` přijímá jen immutable snapshot schválené aktuální revize, allocations a platných approvals. LLM do této cesty nevstupuje. DOM se serializuje jako Windows-1250 a validuje lokálním `schemas/pohoda/2025-10-16/data.xsd`; transitivní závislosti se nikdy nestahují za běhu.

`ExportArtifact` uchovává revizi, vstupní snapshot, verze, XSD výsledek, cestu/hash/velikost XML a hash aktuálního Paperless PDF. Samotné PDF zůstává v Paperless. `ExportBatch` archivuje stabilní ZIP s `invoice-<safe-number>/invoice.xml` a `invoice.pdf` a vlastním SHA-256. Re-export stejné revize vytvoří nový artifact s vazbou na předchozí; změna revize invaliduje použitelnost starého artifactu a vrací workflow ke schválení.

Položky XML vznikají z allocations, ne z assignmentů. U jedné sazby se základ rozdělí metodou largest remainder a DPH je dopočet do schválené hrubé allocation. Kombinace více sazeb a více středisek bez explicitního `Allocation.vat_breakdown` export blokuje. Diagnostický parser POHODA response ukládá výsledek append-only, ale nemění stav; `IMPORTED_TO_POHODA` vyžaduje samostatnou potvrzenou akci správce.

## Paperless snapshot

Approval databáze ukládá pouze `paperless_document_id`, název, čas vytvoření, korespondenta, tagy, OCR text, původní název souboru a diagnostiku synchronizace. PDF zůstává autoritativně v Paperless a endpoint `/api/invoices/{id}/pdf` jej streamuje přes REST API bez trvalé kopie. Nový dokument prochází centralizovaně `NEW → VALIDATION → QUEUE_REVIEW`; AI mezitím používá samostatné stavy `AI_PENDING → AI_PROCESSING → AI_COMPLETED|AI_FAILED`, takže nemění obchodní stav.

Worker při každém discovery cyklu navíc reconciliuje všechny uložené `paperless_document_id`. Jen explicitní REST HTTP 404 mění `source_status` z `AVAILABLE` na `MISSING`; 401/403, timeout, síťová chyba a 5xx ponechávají dostupnost beze změny. Opětovné nalezení dokumentu nastaví `AVAILABLE`. Oba přechody jsou idempotentní a auditované. `MISSING` přidává blocking validation, ale nepřepisuje workflow, revize, approvals ani exportní historii.

`Invoice.disposition` je třetí, samostatná osa: `ACTIVE`, `IGNORED_DUPLICATE`, `IGNORED_OTHER`. Uchovává důvod, komentář, aktéra, čas a volitelný odkaz na původní fakturu. Výchozí fronta zobrazuje jen `ACTIVE + AVAILABLE`; další pohledy explicitně ukazují ignorované a chybějící zdroje.

## AI extrakce a validace

Worker před každým během znovu načte OCR z Paperless REST API. Ollama dostane nedůvěryhodný OCR text oddělený značkami a striktní JSON schema. Pydantic odmítne chybějící nebo neznámá pole; hodnoty mohou být explicitně `null`. Každý běh ukládá model, schema/prompt verzi, raw response, parsed JSON, provenance, dobu, chybu a validační snapshot do `ai_extractions`. První běh lze aplikovat automaticky jen na prázdnou revizi; re-extrakce zůstane kandidátem do explicitního potvrzení.

Deterministická validační služba používá `Decimal`, český checksum IČO, formát DIČ/VS, ISO datum a měnu, matematiku DPH a součtů, účet, IBAN mod-97 a BIC. LLM nevytváří XML/SQL, workflow stav, cost center ani approvera. Detailní kontrakt je v `docs/AI_EXTRACTION.md`.

Český domácí účet prochází jedinou normalizační službou. Kombinovaný vstup `[prefix-]account/bank_code` se rozloží bez hádání číslic; původní text zůstane v `bank_account_raw`. Dodavatelská adresa se strukturuje primárně modelem a konzervativně normalizuje pouze z dodavatelského adresního bloku, nikdy z celého OCR. Volitelný modulo-11 checksum i matematické VAT reconciliation kontroly včetně řádku jsou review WARNING; pouze neplatný/neúplný VAT formát zůstává blocking. `ROUNDING` řádek se eviduje explicitně a deklarované částky se výpočtem nepřepisují.

`QUEUE_MANAGER` vidí celou frontu a smí provádět správcovské změny. `APPROVER` vidí endpoint „Moje úkoly“ a detail/PDF pouze faktur s aktivním assignmentem. Role pocházejí z Keycloak tokenu a backend je kontroluje nezávisle na viditelnosti prvků ve frontendu.

## Revize a approvals

`Invoice.current_revision` ukazuje aktivní snapshot. Allocation ukládá `NUMERIC(18,2)`, volitelné procento, středisko, poznámku a autora. Approval assignment obsahuje fakturu, revizi, allocation, approvera, lifecycle stav a časy přiřazení/rozhodnutí/invalidace. Významná změna vytvoří novou revizi, zkopíruje aktuální návrh allocations a approverů, invaliduje staré assignmenty i decisions, uchová auditní stopu a vrátí fakturu do `NEEDS_REVIEW`.

Rozhodovací transakce nejprve zamkne řádek faktury a potom assignment. Částečný unikátní index dovoluje nejvýše jedno platné rozhodnutí na assignment. Tím jsou double-click i souběžná rozhodnutí deterministická. Finální `APPROVED` vzniká až po kontrole všech aktivních povinných assignmentů aktuální revize. Detailní state machine je v `docs/APPROVAL_WORKFLOW.md`.

## Background joby

PostgreSQL tabulka `processing_jobs` je approval fronta se stavem, omezeným počtem pokusů, lease a idempotency key. Worker vybírá jeden job pomocí `FOR UPDATE SKIP LOCKED`; Ollama má paralelismus 1. Stabilní chyby zahrnují `OLLAMA_UNAVAILABLE`, `OLLAMA_TIMEOUT`, `INVALID_JSON`, `SCHEMA_VALIDATION_FAILED`, `PAPERLESS_ERROR` a `EXTRACTION_FAILED`. Redis je oddělený a používá jej Paperless.

## Důvěryhodné hranice

Browser nikdy nevidí Paperless token ani client secret. Backend drží náhodné opaque session ID v `HttpOnly`, `Secure` (v produkci) a `SameSite=Lax` cookie. Změnové endpointy kontrolují roli a CSRF origin. Exportní archiv je přístupný jen autorizovaným endpointem; normalizované názvy a kontrola resolved cest brání traversal.

## Konzistence klienta a audit požadavků

Frontend používá polling (dashboard 5 s, otevřený detail 3 s, úkoly 5 s) a po mutaci okamžitý refetch. Editační payload nese `expected_revision`; backend při nesouladu vrací HTTP 409 `STALE_REVISION`. Serverový detail a lokální formulářový draft jsou oddělené: čistý draft se hydratuje při změně editovatelných serverových dat i uvnitř stejné revize, zatímco dirty draft se pollingem nepřepisuje a uživatel dostane explicitní volbu načíst serverovou verzi. Číslo revize samo není hydratační signál, protože první AI auto-apply naplňuje počáteční revizi 1.

Middleware přiděluje nebo přebírá `X-Request-ID`. Auditní služba jej spolu s `actor_username` a `actor_roles` přidává do JSON metadata. POHODA generátor odděluje dodavatele ve zdrojové revizi od cílové účetní jednotky v serverové konfiguraci.
