# Aktuální stav

- Datum ověření: 2026-08-24
- Branch: `main`
- Git remote: `git@github-paperless-approval:pavel-holub-geovap/paperless-invoice-approval.git`
- Approval aplikace: `http://172.30.172.167/`
- Nasazeno: PostgreSQL, Redis, Keycloak, Paperless-ngx, Nginx, `approval-backend`, `approval-worker`, `approval-frontend`, Ollama a jednorázový `ollama-pull`. Všechny dlouhodobé služby jsou healthy; provision/bootstrap/pull kontejnery skončily kódem 0.
- Databáze: Approval používá vlastní databázi a credentials. Alembic je na `0006 (head)`. Backend ani worker nemají Paperless DB credentials a komunikují s Paperless pouze přes REST API.
- OIDC: skutečný Authorization Code flow prošel pro `queue-manager`, `approver1`, `approver2` a `approver3`. Approver nemůže otevřít manažerský seznam (HTTP 403).

## Opravná iterace po Etapě F

- Český bankovní účet se po LLM vždy deterministicky normalizuje na `bank_account_raw`, `bank_account_prefix`, `bank_account_number`, zpětně kompatibilní `bank_account` a samostatný `bank_code`. Stejný kombinovaný řetězec v obou LLM polích je bezpečně rozpoznán. Modulo-11 checksum je deterministická WARNING kontrola.
- POHODA generátor `pohoda-received-invoice.v2` používá normalizovanou schválenou revizi. `accountNo` nikdy neobsahuje lomítko/kód a `bankCode` nikdy neobsahuje účet.
- Tři souhrnné kontroly `VAT_BASE_TOTAL_MISMATCH`, `VAT_TOTAL_MISMATCH` a `VAT_TOTAL_MATH` jsou WARNING a nesmějí samy blokovat workflow ani export. Řádková matematika, allocations, chybějící zdroj a ostatní skutečné chyby zůstávají blocking.
- `disposition` je oddělená od workflow: `ACTIVE`, `IGNORED_DUPLICATE`, `IGNORED_OTHER`. Vyřazení/restore jsou auditované, ignorované faktury nelze schválit ani exportovat a Paperless zdroj se nemaže.
- `source_status` je oddělený stav `AVAILABLE`/`MISSING`. Pouze přesné Paperless HTTP 404 označí zdroj jako chybějící; 5xx, timeout a síťová chyba jsou sync error. Missing audit je idempotentní a workflow/PDF/nový export jsou blokované.
- Frontend používá History API routy `/`, `/invoices/:id`, `/approvals`, `/cost-centers` a `/exports`. Manažerské Fronta odkazy, přímý detail a `popstate` jsou testované; backend authorization zůstává autoritativní.

## Reálné uživatelské faktury

- Paperless dokument `2`, `giritonsystems-26071092`, OCR 807 znaků: supplier `GIRITON Systems s.r.o.`, číslo/VS `26071092`, CZK. PDF uvádí účet `2300122535/2010`; revize 6 obsahuje raw `2300122535/2010`, prefix `null`, číslo `2300122535`, kód `2010`, IBAN/BIC `null`. PDF uvádí 3 960,00 + 831,60 = 4 791,60 a zaokrouhlení +0,40 na 4 792,00. Normalizovaná data mají total 4 792,00 a `VAT_TOTAL_MATH` WARNING s expected 4 791,60, actual 4 792,00, difference 0,40.
- Paperless dokument `4`, `giritonsystems-26061111`, OCR 807 znaků: supplier `GIRITON Systems s.r.o.`, číslo/VS `26061111`, CZK. Revize 4 má stejné přesné rozdělení účtu. PDF uvádí 3 995,00 + 838,95 = 4 833,95 a zaokrouhlení +0,05 na 4 834,00. `VAT_TOTAL_MATH` je WARNING s expected 4 833,95, actual 4 834,00, difference 0,05.
- Existující orphan Approval `2f24449b-55c8-4211-890e-66104f0a23d6`, Paperless ID `3`, vrací přímé HTTP 404. Záznam zůstal zachován jako `QUEUE_REVIEW + IGNORED_DUPLICATE + MISSING`, navázaný na fakturu dokumentu 2. Má právě jeden `SOURCE_DOCUMENT_MISSING` audit a blocking validaci; PDF, submit i export vracejí 409.
- Po smoke cleanup jsou v Paperless 3 dokumenty a v Approval 7 auditních invoice záznamů: `AVAILABLE=3`, `MISSING=4`, `ACTIVE=5`, `IGNORED_DUPLICATE=2`, `IGNORED_OTHER=0`. Další missing řádky jsou výhradně vlastní syntetické dokumenty vytvořené a smazané smoke testy; každý má právě jeden source-missing audit.

## Nový POHODA artefakt pro ruční import

- Invoice `8d630d0f-28a3-42ee-bdb1-84ce9c35292c`, Paperless ID 1, invoice revision 45.
- Nový immutable re-export artifact: `39097d07-4672-4709-9a7c-d73653079755`; source artifact `abc9be80-6c5a-4fc8-8493-75c7cd3d3918`; druhý export v tomto řetězci.
- Stažení po přihlášení: `/api/exports/artifacts/39097d07-4672-4709-9a7c-d73653079755/xml`.
- XML: Windows-1250, 3 065 B, SHA-256 `0f661538d4768af882221bac2401b8ace6c9a1f7f83ba5913cc950900b379d9b`, `XSD_VALID`, XSD bundle `2025-10-16`, generator v2.
- Ověřená bankovní XML pole: `typ:accountNo=0000000000`, `typ:bankCode=0000`. Původní artefakty zůstaly immutable a dostupné jako historie.
- Batch `EXP-2026-000003` (`9d56b1a8-d468-464b-93d5-8335b85901b1`) má SHA-256 `5f478f226a4642c251b45f91fa2f3a4dc049c2939cfc28eb27e6b7a6b2302e65`. Import do POHODY nebyl potvrzen; čeká na ruční test.

## Závěrečné automatické ověření

- Backend: 84/84 testů; Ruff čistý.
- Frontend: 5 testovacích souborů, 11/11 testů; TypeScript a produkční Vite build prošly.
- Stage B: OIDC queue-manager/approver1, role, PDF a manažerský endpoint 403 pro approvera prošly.
- Stage D: dvě skutečné inference `qwen3:4b`, bezpečná neaplikovaná re-extrakce a prompt-injection kontrola prošly; business stav se nezměnil.
- Stage E: allocations 700/510 Kč, tři assignments, RETURN/REJECT/REOPEN, invalidace, idempotentní/souběžné approvals, 403 a Paperless tagy prošly; skončilo `APPROVED`.
- Stage F: re-approval, XSD-valid generování/re-export, XML/PDF/ZIP hashe, response parser a bankovní XML semantics prošly; skončilo `EXPORT_CREATED`.
- Opravný smoke: vlastní ID 6/7 prošla Paperless upload → OCR 911/911 → Approval → AI COMPLETED; parser opravil `19-2000145399/0800`, duplicita byla otagována, skryta, blokována, obnovena s auditem a znovu vyřazena. Po odstranění pouze vlastních Paperless ID nastavila reconciliation `MISSING`; PDF/submit/export vrátily 409.
- Deployment proběhl přes `git pull --ff-only`, `docker compose config --quiet`, build, `up -d`, migraci a reconciliation bez mazání databází, auditů nebo volumes.
