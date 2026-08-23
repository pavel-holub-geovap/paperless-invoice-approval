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

## Paperless snapshot

Approval databáze ukládá pouze `paperless_document_id`, název, čas vytvoření, korespondenta, tagy, OCR text, původní název souboru a diagnostiku synchronizace. PDF zůstává autoritativně v Paperless a endpoint `/api/invoices/{id}/pdf` jej streamuje přes REST API bez trvalé kopie. Nový dokument prochází centralizovaně `NEW → VALIDATION → QUEUE_REVIEW`; AI mezitím používá samostatné stavy `AI_PENDING → AI_PROCESSING → AI_COMPLETED|AI_FAILED`, takže nemění obchodní stav.

## AI extrakce a validace

Worker před každým během znovu načte OCR z Paperless REST API. Ollama dostane nedůvěryhodný OCR text oddělený značkami a striktní JSON schema. Pydantic odmítne chybějící nebo neznámá pole; hodnoty mohou být explicitně `null`. Každý běh ukládá model, schema/prompt verzi, raw response, parsed JSON, provenance, dobu, chybu a validační snapshot do `ai_extractions`. První běh lze aplikovat automaticky jen na prázdnou revizi; re-extrakce zůstane kandidátem do explicitního potvrzení.

Deterministická validační služba používá `Decimal`, český checksum IČO, formát DIČ/VS, ISO datum a měnu, matematiku DPH a součtů, účet, IBAN mod-97 a BIC. LLM nevytváří XML/SQL, workflow stav, cost center ani approvera. Detailní kontrakt je v `docs/AI_EXTRACTION.md`.

`QUEUE_MANAGER` vidí celou frontu a smí provádět správcovské změny. `APPROVER` vidí endpoint „Moje úkoly“ a detail/PDF pouze faktur s aktivním assignmentem. Role pocházejí z Keycloak tokenu a backend je kontroluje nezávisle na viditelnosti prvků ve frontendu.

## Revize a approvals

`Invoice.current_revision` ukazuje aktivní snapshot. Allocation ukládá `NUMERIC(18,2)`, volitelné procento, středisko, poznámku a autora. Approval assignment obsahuje fakturu, revizi, allocation, approvera, lifecycle stav a časy přiřazení/rozhodnutí/invalidace. Významná změna vytvoří novou revizi, zkopíruje aktuální návrh allocations a approverů, invaliduje staré assignmenty i decisions, uchová auditní stopu a vrátí fakturu do `NEEDS_REVIEW`.

Rozhodovací transakce nejprve zamkne řádek faktury a potom assignment. Částečný unikátní index dovoluje nejvýše jedno platné rozhodnutí na assignment. Tím jsou double-click i souběžná rozhodnutí deterministická. Finální `APPROVED` vzniká až po kontrole všech aktivních povinných assignmentů aktuální revize. Detailní state machine je v `docs/APPROVAL_WORKFLOW.md`.

## Background joby

PostgreSQL tabulka `processing_jobs` je approval fronta se stavem, omezeným počtem pokusů, lease a idempotency key. Worker vybírá jeden job pomocí `FOR UPDATE SKIP LOCKED`; Ollama má paralelismus 1. Stabilní chyby zahrnují `OLLAMA_UNAVAILABLE`, `OLLAMA_TIMEOUT`, `INVALID_JSON`, `SCHEMA_VALIDATION_FAILED`, `PAPERLESS_ERROR` a `EXTRACTION_FAILED`. Redis je oddělený a používá jej Paperless.

## Důvěryhodné hranice

Browser nikdy nevidí Paperless token ani client secret. Backend drží náhodné opaque session ID v `HttpOnly`, `Secure` (v produkci) a `SameSite=Lax` cookie. Změnové endpointy kontrolují roli a CSRF origin. Exportní archiv je přístupný jen autorizovaným endpointem.
