# Architektura

## Komponenty

- `frontend`: statický React build; používá pouze backend API a serverovou OIDC session.
- `backend`: FastAPI, autorizační kontrola, orchestrace use-cases a OpenAPI.
- `worker`: stejný aplikační balík, databázová fronta, sekvenční Paperless/LLM úlohy.
- `postgres`: oddělené databáze aplikace a Keycloak v jednom persistentním clusteru.
- `keycloak`: centrální OIDC identita s importovaným realm konfigurací.
- `ollama`: lokální, CPU-kompatibilní inference; model se stahuje mimo image a je persistentní.
- `nginx`: jeden vstup pro statické UI a `/api` proxy.
- externí `Paperless`: autorita pro PDF/OCR a hrubé stavové tagy.

## Vrstvy backendu

`api/routes` mapuje HTTP na use-cases; `services` obsahuje workflow, validace, audit, exporty a XML; `integrations` izoluje Paperless/Ollama/OIDC; `models` a `schemas` definují persistence a kontrakty. Route nesmí přímo přepisovat workflow stav.

## Revize a approvals

`Invoice.current_revision` ukazuje aktivní snapshot. Approval assignment obsahuje `invoice_revision_id` a `allocation_id`. Významná změna vytvoří novou revizi/snapshot, označí aktivní decisions jako invalidní, uchová jejich auditní stopu a vrátí fakturu do přípravy nebo `AWAITING_APPROVAL` podle fáze.

## Background joby

PostgreSQL tabulka `processing_jobs` je fronta se stavem, počtem pokusů, lease a idempotency key. Worker vybírá jeden job pomocí `FOR UPDATE SKIP LOCKED`, po timeoutu může lease obnovit. To splňuje provoz na 8GB VM bez Redis.

## Důvěryhodné hranice

Browser nikdy nevidí Paperless token ani client secret. Backend drží náhodné opaque session ID v `HttpOnly`, `Secure` (v produkci) a `SameSite=Lax` cookie. Změnové endpointy kontrolují roli a CSRF origin. Exportní archiv je přístupný jen autorizovaným endpointem.

