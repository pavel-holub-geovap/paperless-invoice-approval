# Architektura

## Komponenty

- `frontend`: statický React build; používá pouze backend API a serverovou OIDC session.
- `backend`: FastAPI, autorizační kontrola, orchestrace use-cases a OpenAPI.
- `worker`: stejný aplikační balík; v Etapách B/C periodicky synchronizuje tagged dokumenty z Paperless REST API a obsluhuje databázovou frontu stavových tagů. LLM větev je vypnutá.
- `postgres`: oddělené databáze a credentials pro approval aplikaci, Keycloak a Paperless v jednom persistentním clusteru.
- `redis`: persistentní broker/cache pouze pro izolovaný testovací Paperless; approval fronta zůstává v PostgreSQL.
- `paperless`: izolovaná testovací autorita pro originální PDF, OCR, metadata a stavové tagy; runtime integrace probíhá výhradně přes REST API.
- `keycloak`: centrální OIDC identita s importovaným realm konfigurací.
- `ollama`: opt-in Compose profil `llm`; lokální CPU inference se nasazuje až v samostatné Etapě D.
- `nginx`: jediná publikovaná vstupní vrstva pro Approval (`:80`), Paperless (`:8000`) a Keycloak (`:8081`).
- Produkční varianta může později nahradit testovací Paperless existující externí instancí bez přímého DB propojení.

## Vrstvy backendu

`api/routes` mapuje HTTP na use-cases; `services` obsahuje workflow, validace, audit, exporty a XML; `integrations` izoluje Paperless/Ollama/OIDC; `models` a `schemas` definují persistence a kontrakty. Route nesmí přímo přepisovat workflow stav.

## Paperless snapshot

Approval databáze ukládá pouze `paperless_document_id`, název, čas vytvoření, korespondenta, tagy, OCR text, původní název souboru a diagnostiku synchronizace. PDF zůstává autoritativně v Paperless a endpoint `/api/invoices/{id}/pdf` jej streamuje přes REST API bez trvalé kopie. Nový dokument prochází centralizovaně `NEW → VALIDATION → QUEUE_REVIEW`; worker v této fázi nevytváří LLM job.

`QUEUE_MANAGER` vidí celou frontu a smí provádět správcovské změny. `APPROVER` vidí endpoint „Moje úkoly“ a detail/PDF pouze faktur s aktivním assignmentem. Role pocházejí z Keycloak tokenu a backend je kontroluje nezávisle na viditelnosti prvků ve frontendu.

## Revize a approvals

`Invoice.current_revision` ukazuje aktivní snapshot. Approval assignment obsahuje `invoice_revision_id` a `allocation_id`. Významná změna vytvoří novou revizi/snapshot, označí aktivní decisions jako invalidní, uchová jejich auditní stopu a vrátí fakturu do přípravy nebo `AWAITING_APPROVAL` podle fáze.

## Background joby

PostgreSQL tabulka `processing_jobs` je approval fronta se stavem, počtem pokusů, lease a idempotency key. Worker vybírá jeden job pomocí `FOR UPDATE SKIP LOCKED`, po timeoutu může lease obnovit. Redis je oddělený a používá jej Paperless.

## Důvěryhodné hranice

Browser nikdy nevidí Paperless token ani client secret. Backend drží náhodné opaque session ID v `HttpOnly`, `Secure` (v produkci) a `SameSite=Lax` cookie. Změnové endpointy kontrolují roli a CSRF origin. Exportní archiv je přístupný jen autorizovaným endpointem.
