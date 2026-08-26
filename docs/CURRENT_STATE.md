# Aktuální stav

- Datum ověření: 2026-08-26
- Branch: `main`
- Git remote: `git@github-paperless-approval:pavel-holub-geovap/paperless-invoice-approval.git`
- Approval aplikace: `http://172.30.172.167/`
- Nasazeno: PostgreSQL, Redis, Keycloak, Paperless-ngx, Nginx, `approval-backend`, `approval-worker`, `approval-frontend`, Ollama a jednorázový `ollama-pull`. Všechny dlouhodobé služby jsou healthy; provision/bootstrap/pull kontejnery skončily kódem 0.
- Databáze: Approval používá vlastní databázi a credentials. Alembic je na `0008 (head)`. Backend ani worker nemají Paperless DB credentials a komunikují s Paperless pouze přes REST API.
- OIDC: skutečný Authorization Code flow prošel pro `queue-manager`, `approver1`, `approver2` a `approver3`. Approver nemůže otevřít manažerský seznam (HTTP 403).

## Opravná iterace po Etapě F

- Český bankovní účet se po LLM vždy deterministicky normalizuje na `bank_account_raw`, `bank_account_prefix`, `bank_account_number`, zpětně kompatibilní `bank_account` a samostatný `bank_code`. Stejný kombinovaný řetězec v obou LLM polích je bezpečně rozpoznán. Modulo-11 checksum je deterministická WARNING kontrola.
- POHODA generátor `pohoda-received-invoice.v3` používá normalizovanou schválenou revizi. `dataPack/@ico` je cílové IČO z konfigurace; `accountNo` nikdy neobsahuje lomítko/kód a `bankCode` nikdy neobsahuje účet.
- Tři souhrnné kontroly `VAT_BASE_TOTAL_MISMATCH`, `VAT_TOTAL_MISMATCH` a `VAT_TOTAL_MATH` jsou WARNING a nesmějí samy blokovat workflow ani export. Řádková matematika, allocations, chybějící zdroj a ostatní skutečné chyby zůstávají blocking.
- `disposition` je oddělená od workflow: `ACTIVE`, `IGNORED_DUPLICATE`, `IGNORED_OTHER`. Vyřazení/restore jsou auditované, ignorované faktury nelze schválit ani exportovat a Paperless zdroj se nemaže.
- `source_status` je oddělený stav `AVAILABLE`/`MISSING`. Pouze přesné Paperless HTTP 404 označí zdroj jako chybějící; 5xx, timeout a síťová chyba jsou sync error. Missing audit je idempotentní a workflow/PDF/nový export jsou blokované.
- Frontend používá History API routy `/`, `/invoices/:id`, `/approvals`, `/cost-centers` a `/exports`. Manažerské Fronta odkazy, přímý detail a `popstate` jsou testované; backend authorization zůstává autoritativní.

## Nahrávání faktur z Approval aplikace

- `QUEUE_MANAGER` může nahrát jeden nebo více PDF přes dashboard. Browser posílá každý soubor samostatně do Approval endpointu `POST /api/uploads`; Paperless token zůstává pouze na backendu. `APPROVER` endpoint použít nesmí.
- Backend přijímá pouze PDF do konfigurovatelného limitu `UPLOAD_MAX_BYTES` (výchozí 8 MiB), kontroluje příponu, MIME i PDF signaturu, počítá SHA-256, sanitizuje název a ukládá pouze metadata. Originální PDF trvale neduplikuje.
- Paperless upload používá oficiální `POST /api/documents/post_document/`. Worker sleduje Paperless task, OCR, vznik Approval invoice a existující AI pipeline. Opakování se stejným idempotency klíčem a hashem je bezpečné; nejednoznačný timeout po odeslání se automaticky neopakuje.
- Skutečný smoke vytvořil `codex-approval-upload-6988633cde.pdf`: upload `01fd4ab9-122b-4356-99b0-88cc33751554`, Paperless task `0138235d-8a75-4c39-9a71-220a6683158e`, Paperless document `19` a Approval invoice `1246c15e-44c6-4596-8731-8bbf4315309d`. OCR má 911 znaků, AI skončila `AI_COMPLETED` na `qwen3:8b`, workflow `QUEUE_REVIEW` a UI status `READY_FOR_REVIEW`.
- Audit `DOCUMENT_UPLOADED_TO_PAPERLESS` obsahuje `queue-manager`, subject, korelační ID, sanitizovaný název, velikost, MIME a Paperless document ID. Invoice nese `uploaded_by=queue-manager`, zdrojový i Approval timestamp a SHA-256 přijatých bytů `84ee2c5f6f96635cb5925cdb6abd1476833c9f59977c093a9ec2be60e7f229f7`.
- Originální PDF dokumentu 19 se přes Approval proxy zobrazilo/stáhlo. Fronta se v otevřené session aktualizovala bez F5. Skutečný paralelní smoke navíc nezávisle přijal tři PDF jako Paperless dokumenty 20, 21 a 22; neplatný textový soubor vrátil HTTP 415 a pokus uživatele `approver1` HTTP 403.

## Reálné uživatelské faktury

### Qwen3 8B raw schema a diagnostika

- Původní `SCHEMA_VALIDATION_FAILED` na Paperless dokumentu `17`, extraction revision 1, prompt v4 vznikl proto, že Qwen vynechal povinné `total_amount`, místo něj vrátil extra `total_with_vat={"value":469,"source_text":"469,00"}` a přidal sedm dalších nepovolených polí. Starý kód validoval odpověď přímo jako `InvoiceExtractionV1`, zahodil raw odpověď i devět detailních Pydantic chyb a uložil jen obecné `Ollama JSON does not match InvoiceExtractionV1`. Historickou raw odpověď proto nelze zpětně obnovit.
- Pipeline nyní používá prompt v5 a samostatné `invoice-extraction.raw.v1`: ploché Ollama structured-output schéma přijímá u známých raw hodnot string/null, následuje konzervativní deterministická normalizace a teprve potom přísné `invoice-extraction.v3`. Raw všech pokusů, přesné chyby, normalizační změny a počet corrective retry jsou uloženy v extraction revision. Nejvýše jeden retry dostane konkrétní validační feedback; schema/request chyby už nevyvolávají tři slepé job retry.
- Skutečný více-dokumentový smoke po deploymentu prošel. Dokument `17`, revision 3, `qwen3:8b`, trval 1 057 602 ms a potřeboval jeden retry kvůli `vat_lines.0.adjustment_type="ZakladCZK"` (očekáváno `ROUNDING` nebo null). Normalizace mimo jiné provedla `"81,40"→81.40`, `"469,00"→469.00`, `"387,60"→387.60` a `"76001"→"760 01"`; finální `total_amount=469.00` a `InvoiceExtractionV1` validace prošla.
- Dokument `14`, revision 3, trval 1 065 816 ms a potřeboval jeden retry kvůli `vat_lines.0.adjustment_type="line-item"`; normalizoval `"21%"→21` a `"14000"→"140 00"`. Dokument `11`, revision 5, prošel bez retry za 467 344 ms. Všechny tři mají zachovaný raw výstup, raw schema v1, kanonickou validaci `PASSED`; kandidáti nebyli aplikováni a workflow zůstalo postupně `QUEUE_REVIEW`, `QUEUE_REVIEW`, `READY_FOR_EXPORT`.

- Paperless dokument `14`, GMtech faktura `20260182`, byl znovu vytěžen skutečným Qwen3 8B přes prompt `invoice-extraction.cs-en.v4`. Historická extraction revision 1 zůstala zachována s chybným DUZP `2026-07-08`; raw model revision 2 už vrátil vystavení `2026-07-08`, DUZP `2026-06-30` a splatnost `2026-08-07`. Kandidát byl standardním API aplikován jako invoice revision 2. DB i API mají stejné ISO hodnoty, provenance DUZP je `Datum zd. plnění: 30.06.2026` a append-only `FIELD_CHANGED` audit obsahuje old `2026-07-08`, new `2026-06-30`, extraction ID a uživatele `queue-manager`. Inference trvala 485 521 ms.
- Paperless dokument `11`, GIRITON faktura `25081151`, byl 2026-08-24 znovu vytěžen Qwen3 8B přes schema/prompt v3 a deterministicky doplněn z vytištěné VAT tabulky a sumáře. Dodavatel je `GIRITON Systems s.r.o.`, raw adresa `Hornosušská 1399/4 735 64 Havířov - Prostřední Suchá`, street `Hornosušská 1399/4`, ZIP `735 64`, city `Havířov - Prostřední Suchá`; účet je `2300122535/2010` rozdělený na `2300122535` a `2010`.
- Po opravě sémantiky cílové jednotky byl pro tuto fakturu vytvořen nový immutable artifact `7e44e832-a795-4771-844e-e261c494aa75`. XML skutečně stažené běžným endpointem má SHA-256 `4689d1a8d57001aebc2ed243defd4db86e53dd96e3b5300ed6efa16211186f28`, `dataPack/@ico=15049248`, bez `dataPack/@key`, dodavatelské `partnerIdentity/address/ico=28652240`, stav `XSD_VALID` a samostatný stav `TARGET_UNIT_VALID`.
- Přesné DPH řádky dokumentu 11 jsou hlavní základ `4065.00`, DPH `853.65`, gross `4918.65` a zaokrouhlení základ `0.29`, DPH `0.06`, gross `0.35`. Vytištěné součty jsou základ `4065.29`, DPH `853.71`, částka `4919.00`. Oba `VAT_ROW_OK` a všechny tři součtové kontroly jsou OK; `VAT_ROUNDING_ADJUSTMENT` je informativní WARNING, žádná DPH reconciliation není blocking.
- Kandidát extraction revision 4 byl explicitně aplikován jako invoice revision 2, rozúčtován na středisko 200, potvrzen proti originálu a schválen skutečným `approver1`. Immutable artifact `fea7823e-b260-4b70-a166-591c34960f7e` je `XSD_VALID`, SHA-256 `4689d1a8d57001aebc2ed243defd4db86e53dd96e3b5300ed6efa16211186f28`; XML obsahuje přesnou adresu, účet/kód banky a součty 4065.29 + 853.71 = 4919.00. Import do POHODY nebyl potvrzen.

- Paperless dokument `2`, `giritonsystems-26071092`, OCR 807 znaků: supplier `GIRITON Systems s.r.o.`, číslo/VS `26071092`, CZK. PDF uvádí účet `2300122535/2010`; revize 6 obsahuje raw `2300122535/2010`, prefix `null`, číslo `2300122535`, kód `2010`, IBAN/BIC `null`. PDF uvádí 3 960,00 + 831,60 = 4 791,60 a zaokrouhlení +0,40 na 4 792,00. Normalizovaná data mají total 4 792,00 a `VAT_TOTAL_MATH` WARNING s expected 4 791,60, actual 4 792,00, difference 0,40.
- Paperless dokument `4`, `giritonsystems-26061111`, OCR 807 znaků: supplier `GIRITON Systems s.r.o.`, číslo/VS `26061111`, CZK. Revize 4 má stejné přesné rozdělení účtu. PDF uvádí 3 995,00 + 838,95 = 4 833,95 a zaokrouhlení +0,05 na 4 834,00. `VAT_TOTAL_MATH` je WARNING s expected 4 833,95, actual 4 834,00, difference 0,05.
- Existující orphan Approval `2f24449b-55c8-4211-890e-66104f0a23d6`, Paperless ID `3`, vrací přímé HTTP 404. Záznam zůstal zachován jako `QUEUE_REVIEW + IGNORED_DUPLICATE + MISSING`, navázaný na fakturu dokumentu 2. Má právě jeden `SOURCE_DOCUMENT_MISSING` audit a blocking validaci; PDF, submit i export vracejí 409.
- Po smoke cleanup jsou v Paperless 3 dokumenty a v Approval 7 auditních invoice záznamů: `AVAILABLE=3`, `MISSING=4`, `ACTIVE=5`, `IGNORED_DUPLICATE=2`, `IGNORED_OTHER=0`. Další missing řádky jsou výhradně vlastní syntetické dokumenty vytvořené a smazané smoke testy; každý má právě jeden source-missing audit.

## Aktuální POHODA artefakt pro ruční import

- Invoice `8d630d0f-28a3-42ee-bdb1-84ce9c35292c`, Paperless ID 1, invoice revision 59.
- Aktuální immutable re-export artifact: `bd7fddd2-a0b2-4282-9e3b-2f39d0de840c`; source artifact `f9aa83bb-4a1d-4623-8cdf-58b4bd043f53`. Předchozí artefakty zůstaly immutable a dostupné jako historie.
- Stažení po přihlášení: `/api/exports/artifacts/bd7fddd2-a0b2-4282-9e3b-2f39d0de840c/xml`.
- XML: Windows-1250, 3 080 B, SHA-256 `c079a47c7fd5db7c22da5a44c1b141760d51abcd4a9792f3de6f6dd5b67db442`, `XSD_VALID`, XSD bundle `2025-10-16`, generátor `pohoda-received-invoice.v3`.
- Cílová účetní jednotka je explicitně `dataPack/@ico=15049248`, prázdný `key` se negeneruje. Dodavatel zůstává odděleně v `invoiceHeader/partnerIdentity` (`ICO=00000019`, `DIC=CZ00000019`). Ověřená bankovní pole jsou `typ:accountNo=0000000000`, `typ:bankCode=0000`.
- Rozúčtování 700/510 Kč používá střediska 200/300. Batch `31d0793d-a463-4df3-bf3b-8b81daf318cc` má ZIP SHA-256 `8a79909a...`. Odpověď POHODA byla bezpečně parsována; import nebyl potvrzen a čeká na ruční test.

## Závěrečné automatické ověření

- Backend: 135/135 testů; Ruff čistý. AI hranice navíc přijímá pouze jednoznačné lokalizované numerické řetězce modelu (`21%`, desetinná čárka, mezery tisíců a běžný měnový suffix), striktně odděluje označené české datumy včetně provenance a stále odmítá jiné neschématické hodnoty. Regrese pokrývají RawV1, serializované ploché Ollama schéma, přesnou diagnostiku, zachování dvou neúspěšných raw pokusů, nejvýše jeden corrective retry a upload validaci, autorizaci, idempotenci i Paperless task polling.
- Frontend: 7 testovacích souborů, 29/29 testů; TypeScript a produkční Vite build prošly. Regrese navíc ověřuje manažerské zobrazení pole, skutečné hodnoty, očekávání, zprávy, pokusu, zachování raw odpovědi a počtu retry i výběr/drag-and-drop více PDF, individuální stavy, retry a aktualizaci bez F5.
- Stage B: OIDC queue-manager/approver1, role, PDF a manažerský endpoint 403 pro approvera prošly.
- Stage D/Qwen3 8B: skutečné inference proběhly na Paperless dokumentech 1, 2, 4, 8, 11, 14 a 17. Nejnovější RawV1 regrese nad dokumenty 17/14/11 skončila třikrát `AI_COMPLETED`, kanonická validace vždy prošla a workflow se nezměnilo. Starší úplná regrese nad dokumentem 1 měla inference 258 136 ms a 254 989 ms, prompt-injection 262 097 ms, 12 OK / 0 WARNING / 0 blocking a zachovala stav `EXPORT_CREATED`. Kandidáti se bez potvrzení neaplikovali a prompt injection nezměnila žádné pole.
- Stage E: allocations 700/510 Kč, tři assignments, RETURN/REJECT/REOPEN, invalidace, idempotentní/souběžné approvals, 403 a Paperless tagy prošly; skončilo `APPROVED`.
- Stage F: re-approval, XSD-valid generování/re-export, XML/PDF/ZIP hashe, response parser a bankovní XML semantics prošly; skončilo `EXPORT_CREATED`.
- Živá aktualizace: samostatná session `approver1` schválila úkol, zatímco manager session zůstala otevřená bez ručního reloadu; polling změnu zobrazil po 116 ms. Následný cleanup schválil zbývající úkoly a Stage F vytvořila aktuální export.
- Audit: mutace a stažení nesou subject, username, role, revizi a korelační ID. Ověřeny byly mimo jiné `INVOICE_FIELD_CHANGED`, `APPROVED`, `XML_GENERATION_REQUESTED`, `EXPORT_DOWNLOADED`, `PDF_DOWNLOADED` a `ZIP_DOWNLOADED`.
- Opravný smoke: vlastní ID 6/7 prošla Paperless upload → OCR 911/911 → Approval → AI COMPLETED; parser opravil `19-2000145399/0800`, duplicita byla otagována, skryta, blokována, obnovena s auditem a znovu vyřazena. Po odstranění pouze vlastních Paperless ID nastavila reconciliation `MISSING`; PDF/submit/export vrátily 409.
- Deployment proběhl přes `git pull --ff-only`, `docker compose config --quiet`, build, `up -d`, migraci a reconciliation bez mazání databází, auditů nebo volumes.

Opravná iterace přidala korelační audit stažení a mutací, ochranu stale revizí, workflow stepper, inline chyby a pending stavy, Prague timestamps a polling bez přepisování rozepsaných dat. Skutečné OIDC, Paperless, Qwen3 8B, souběžné session a POHODA exportní smoke testy prošly.
