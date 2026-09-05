# Aktuální stav

## Approver upload a revizní queue review

- `APPROVER` používá stejný `/api/uploads` BFF jako queue manager; metadata nesou `actor_role` a faktura `upload_origin`, stabilní uploader subject i username.
- Existující `CostCenter` je v českém UI „Sekce“. Explicitní `ApproverSectionPermission` podporuje M:N grant/revoke, audit a kontrolu aktuálního oprávnění při každém novém rozhodnutí.
- Uploader vidí vlastní dokument už před assignmentem, smí opravit data a rozdělit jej pouze do povolených sekcí. Vzniká normální vlastní assignment/decision.
- Self-approval není finální gate. Předání a kontrola queue-managera jsou uloženy na konkrétní revizi; správcovská změna po předání vytvoří novou revizi a zachová předchozí rozhodnutí jako invalidovanou historii.
- Schvalovatelská navigace obsahuje „Ke schválení“, „Moje historie“ a „Moje nahrané“ s upload boxem. Manager grid rozlišuje approver-upload a stav kontroly.
- Živý smoke na `ubuntudocker` 2026-09-05 prošel přes veřejné OIDC relace `queue-manager` a `approver1`: approver nahrál syntetické PDF jako Paperless dokument `55`, OCR má 911 znaků, nepovolená sekce skončila HTTP 409 a povolená sekce vytvořila vlastní assignment. Self-approval dokument neuzavřel, předání správci prošlo a správcovská změna typu vytvořila revizi 2 se zachovaným invalidovaným rozhodnutím revize 1. Překlasifikovaná zálohová faktura má POHODA metodu `NONE`.
- Na integrační VM je Alembic `0011 (head)`, všech 9 dlouhodobých služeb je healthy a všechny 3 provisioning joby skončily `exited/0`.

## Shared Docker host bootstrap (2026-09-02)

- Compose detekce nyní preferuje `docker compose`, akceptuje každou major verzi
  >= 2 (cíleně otestována v2 i v5.1.3) a standalone `docker-compose` >= 2 používá
  jen jako fallback. Samostatná v1.29.2 je odmítnuta a její paralelní instalace
  vedle funkčního v5 pluginu nemění volbu.
- Host bindy jsou explicitní `APP_HOST_PORT`, `PAPERLESS_HOST_PORT` a
  `KEYCLOAK_HOST_PORT`. Public URL zůstávají samostatné kvůli OIDC/proxy/NAT;
  rozdíl portů je varování. Legacy `*_HTTP_PORT` bootstrap mapuje s deprecation
  warningem. Skutečný `docker compose config --format json` prošel s porty
  28080/28000/28081 bez jiných published portů.
- Preflight proti běžícímu hlavnímu stacku odmítl cizí bind 8081 ještě před
  vytvořením Docker objektů a doporučil změnu `KEYCLOAK_HOST_PORT`.
- Izolovaný projekt `paperless-invoice-shared-20260902` naběhl vedle hlavního
  stacku na 18080/18000/18081: 9/9 služeb healthy, 3/3 joby `exited/0`, Alembic
  `0010`, Qwen dostupný, Approval/backend/worker/Keycloak metadata HTTP 200 a
  OIDC login 302 na Keycloak 18081 s callbackem na Approval 18080. Vlastní data
  měla project-scoped volumes; sdílená byla pouze explicitní Ollama cache.
- Keycloak provisioning používá přímo `http://keycloak:8080`. Paperless
  healthcheck používá lokální `/api/`, nesleduje veřejný redirect a má realistický
  startup budget. Bootstrap toleruje 60 sekund přechodného unhealthy stavu a při
  trvalém selhání vypíše health historii i omezený log.
- Po testu byla druhá instance pouze zastavena; 12 kontejnerů, osm vlastních
  volumes, env a data zůstaly zachovány. Hlavní projekt zůstal 9/9 healthy,
  3/3 joby `exited/0`, `status.sh` PASS a veřejný Approval HTTP 200. Žádný cizí
  Docker objekt, volume, databáze ani dokument nebyl odstraněn.
- Cílené lokální testy: bootstrap support 19/19, Ruff PASS, statická Compose
  validace PASS. Úplná business regrese nebyla spuštěna, protože se aplikační
  business kód nezměnil.

## Čistý idempotentní testovací bootstrap (2026-09-01)

- Nový Linux host lze připravit přes `generate-test-env.sh`, read-only
  `bootstrap-test.sh --check` a jediný idempotentní `bootstrap-test.sh`. Samostatný
  `status.sh` ověřuje 9 dlouhodobých služeb, 3 jednorázové joby, Alembic, DB,
  Keycloak/OIDC, Paperless REST/tagy, Qwen3 a oba XSD bundle bez změny stavu.
- Skutečný čistý Compose projekt `paperless-invoice-clean-20260901` vznikl s
  vlastními náhodnými secrets, sítěmi, PostgreSQL/Redis/Paperless/API-token/export
  volumes a porty 18080/18000/18081. Sdílel pouze explicitně povolenou
  neautoritativní 5,2GB Ollama cache. První bootstrap z prázdných databází i
  následný status skončily `PASS`; Alembic je `0010 (head)`.
- Druhý bootstrap prošel beze změny dat. Po bezpečném zastavení přesně clean
  workeru jej stejný příkaz obnovil jako healthy a znovu úspěšně ověřil
  provisioning i migrace. Žádný volume, dokument, audit ani databáze nebyly
  odstraněny.
- Plný syntetický smoke přihlásil `queue-manager` i `approver1`. Paperless dokument
  2 má OCR délky 911 a jeden skutečný `qwen3:8b` běh skončil `AI_COMPLETED`.
  Dokument 3 má validní vložený ISDOC 6.0.2, `extraction_source=ISDOC` a nulový
  počet AI běhů.
- Čistý test odhalil a opravil centrální stahování Paperless archivní OCR varianty
  místo originálu. Všechna PDF čtení nyní používají podporovaný REST parametr
  `original=true`; živý worker jej potvrdil a hash/ISDOC/AI tok prošel. Regrese
  parsuje request query parametr.
- Paralelní čisté kontejnery byly po důkazu pouze zastaveny; jejich volumes a
  syntetická historie zůstaly zachovány pro audit nebo opakované spuštění.
  Hlavní testovací stack poté znovu prošel `status.sh`: všech 9 služeb healthy,
  3 joby `exited/0`, Alembic `0010`, Qwen3 a runtime smoke `PASS`.
- Clean konfigurace záměrně neměla `POHODA_TARGET_ICO`, takže pouze generovaný XML
  export byl bezpečně vypnutý. Hlavní testovací stack nadále ověřil cílové IČO
  `15049248`. Jediným host warningem je chybějící kernel swap-limit capability;
  kontejnery používají memory limity bez swap limitu a test nevyvolal OOM.

## Klasifikace, ISDOC a schválená PDF kopie

- Migrace `0010` přidává ortogonální osy `document_type`, `processing_mode`, `extraction_source`, `isdoc_status`, `approved_pdf_status` a `pohoda_import_method`. Historické faktury jsou bezpečně backfillnuty jako `RECEIVED_INVOICE + FOR_APPROVAL`; nové dokumenty začínají `UNCLASSIFIED`.
- Worker kontroluje attachments před AI. Validní ISDOC Invoice 6.0.2 projde bezpečným XML parserem a lokálním oficiálním XSD bundle, vytvoří immutable extraction snapshot s přesnou provenance `ISDOC` a nepovolí Qwen3. Číslo faktury se mapuje výhradně z namespace-aware root cesty `Invoice/ID`; IČO dodavatele a IDs položek mají vlastní explicitní cesty. Chybějící/nevalidní ISDOC pokračuje přes OCR + AI.
- Finální approval vytváří idempotentní schválené PDF v Paperless pod technickým tagem. Originální PDF zůstává byte-for-byte stejné, razítko je v rozšířeném spodním pásu a všechny attachments včetně ISDOC musí mít stejné SHA-256.
- POHODA metoda je `PDF_ISDOC`, `GENERATED_XML`, nebo `NONE`. Zálohové faktury a centrální/ostatní doklady jsou backendem blokované. Allocations se už nemapují na účetní položky/střediska; jsou pouze informativní.
- Manager může existující Paperless originál bezpečně znovu zpracovat přes auditovaný ISDOC reprocess job. Přechod z dřívějších OCR/AI dat na validní ISDOC vytvoří novou invoice revision a zachová historické AI extraction i audit.

### Skutečná regrese iDoklad / Pixel Design

- Paperless dokument `50`, Approval invoice `5ea0bd1a-7694-42de-9693-7cc242252455` a attachment `Vydaná faktura - 260104-invoice.isdoc` byly bezpečně znovu zpracovány bez duplicitního uploadu. Oficiální XSD 6.0.2 i semantic mapping prošly: `Invoice/ID=260104`, supplier IČO `06668712`, VS `260104`, data `2026-03-02 / 2026-03-02 / 2026-03-09`, částky `4300.00 + 903.00 = 5203.00` a účet `115-5596880207/0100`.
- Stav je `VALID`, zdroj `ISDOC`, provenance čísla dokladu je `/Invoice/ID`. Vznikla invoice revision 2 a immutable ISDOC extraction `bbbce28f-9c2f-436f-9267-1f86055758e0`; historický jediný AI běh zůstal zachován, nový Qwen běh nevznikl. Validace neobsahuje `VAT_ROUNDING_ADJUSTMENT`.
- Po skutečném approval vznikl artifact `fc57891e-c708-4f5c-b94a-e084958594ca` a Paperless derived dokument `51`. Originál má SHA-256 `655560654f11a127b50b5afd3c41ea40cc3edf87c9f55f0a30262050f03abcb8`; embedded ISDOC v originálu i approved PDF má shodně `0eedab709f99f22d76a994d3d2b7f2a7244150b432c2982d8b3c4c7245453748`. Importní metoda je `PDF_ISDOC` a generated POHODA XML je backendem odmítnuto HTTP 409.

- Datum ověření: 2026-08-26
- Branch: `main`
- Git remote: `git@github-paperless-approval:pavel-holub-geovap/paperless-invoice-approval.git`
- Approval aplikace: `http://172.30.172.167/`
- Nasazeno: PostgreSQL, Redis, Keycloak, Paperless-ngx, Nginx, `approval-backend`, `approval-worker`, `approval-frontend`, Ollama a jednorázový `ollama-pull`. Všechny dlouhodobé služby jsou healthy; provision/bootstrap/pull kontejnery skončily kódem 0.
- Databáze: Approval používá vlastní databázi a credentials. Alembic je na `0009 (head)`. Backend ani worker nemají Paperless DB credentials a komunikují s Paperless pouze přes REST API.
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
- Dashboard nyní používá hlavní invoice tabulku jako jediný trvalý seznam. Historických deset uploadů se při otevření nenačítá; právě spuštěná dávka je pouze dočasná, úspěšná položka po potvrzení invoice v tabulce zmizí a chyba zůstane s retry/zavřením. `+ Nahrát fakturu` a `Obnovit` sdílejí na desktopu jeden action bar se stejnou výškou a baseline; refresh má disabled/loading stav.
- Smoke po GUI úpravě vytvořil `codex-approval-upload-d7585a86c8.pdf`, Paperless document `25` a Approval invoice `c2937073-4b2e-44d7-a5c3-5d048d2e5ca2`; OCR má 911 znaků, AI skončila `AI_COMPLETED` a workflow `QUEUE_REVIEW`. Multi-upload vytvořil samostatné dokumenty 26–28 a neplatný soubor vrátil 415. Kontrolovaný výpadek vrátil u uploadu `a91b8d0d-70a2-45d3-9222-d5ea5b82ad3c` stav `FAILED_RETRYABLE/PAPERLESS_UNAVAILABLE`; retry stejným klíčem zachoval upload ID, nastavil `retry_count=1` a vytvořil Paperless document 29 a invoice `570cb439-35a9-4382-a975-8ea05a400ff8`.

## Reálné uživatelské faktury

### Qwen3 8B raw schema a diagnostika

- Původní `SCHEMA_VALIDATION_FAILED` na Paperless dokumentu `17`, extraction revision 1, prompt v4 vznikl proto, že Qwen vynechal povinné `total_amount`, místo něj vrátil extra `total_with_vat={"value":469,"source_text":"469,00"}` a přidal sedm dalších nepovolených polí. Starý kód validoval odpověď přímo jako `InvoiceExtractionV1`, zahodil raw odpověď i devět detailních Pydantic chyb a uložil jen obecné `Ollama JSON does not match InvoiceExtractionV1`. Historickou raw odpověď proto nelze zpětně obnovit.
- Pipeline nyní používá prompt v6 a samostatné `invoice-extraction.raw.v1`: ploché Ollama structured-output schéma přijímá u známých raw hodnot string/null, následuje konzervativní deterministická normalizace a teprve potom přísné `invoice-extraction.v3`. Raw všech pokusů, přesné chyby, normalizační změny a počet corrective retry jsou uloženy v extraction revision. Nejvýše jeden retry dostane konkrétní validační feedback; schema/request chyby už nevyvolávají tři slepé job retry. `ROUNDING` je pouze návrh modelu a kanonicky se přijme jen s explicitním řádkovým štítkem; souhrnné CELKEM/Základ/DPH evidence se odmítá.
- Skutečná Pixel Design faktura, Paperless dokument `24`, byla po nasazení znovu vytěžena Qwen3 8B. Extraction revision `3` (prompt v6, 536 630 ms) je aplikovaná a raw zůstává zachován. Qwen vrátil běžný VAT řádek `4300.00 / 903.00 / 5203.00` s `adjustment_type="none"`; kanonická normalizace použila `null`. Aktuální data mají rozdíl `0.00`, obsahují `VAT_ROW_OK`, `VAT_BASE_TOTAL_OK`, `VAT_TOTAL_OK`, `TOTAL_MATH_OK` a neobsahují `VAT_ROUNDING_ADJUSTMENT`. Historická revision `1` s falešným `ROUNDING` a evidence `Sazba DPH Základ Výše DPH Celkem` zůstala append-only zachována. Reálný GIRITON dokument `11` současně drží explicitní `0.29 / 0.06 / 0.35` a příslušný warning.
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

- Backend: 189/189 testů; úplná sada obsahuje také regresní testy bootstrapu, skutečného Paperless originálu, historie, Paperless fulltext průniku, historického RBAC, chybějícího originálu a složených filtrů. Frontend: 39/39 testů a production build. Ruff je čistý. AI hranice navíc přijímá pouze jednoznačné lokalizované numerické řetězce modelu (`21%`, desetinná čárka, mezery tisíců a běžný měnový suffix), striktně odděluje označené české datumy včetně provenance a stále odmítá jiné neschématické hodnoty.

## Moje historie schvalovatele

APPROVER má vedle aktuální fronty „Ke schválení“ paginovanou „Moji historii“. Jedna faktura je jeden řádek, zatímco detail zobrazuje všechny vlastní assignmenty, allocation, středisko, částku, rozhodnutí, revizi a případnou pozdější invalidaci. Detail je pouze pro čtení a jasně odděluje historické rozhodnutí od aktuálního workflow stavu.

Vyhledávání kombinuje strukturovaná Approval data s OCR fulltextem Paperless. Paperless vrací pouze kandidátní document ID; Approval backend provede průnik s množinou faktur, ke kterým měl přihlášený subject někdy historický assignment. Stejná centrální autorizace chrání history detail a PDF proxy. `MISSING` fakturu z historie neodstraní, pouze znepřístupní originální PDF.
- Frontend: 8 testovacích souborů, 39/39 testů; TypeScript a produkční Vite build prošly. Regrese navíc ověřuje manažerské zobrazení pole, skutečné hodnoty, očekávání, zprávy, pokusu, zachování raw odpovědi a počtu retry i výběr/drag-and-drop více PDF, individuální dočasné stavy, retry, jedinou permanentní invoice tabulku, společný action bar a aktualizaci bez F5.
- Stage B: OIDC queue-manager/approver1, role, PDF a manažerský endpoint 403 pro approvera prošly.
- Stage D/Qwen3 8B: skutečné inference proběhly na Paperless dokumentech 1, 2, 4, 8, 11, 14 a 17. Nejnovější RawV1 regrese nad dokumenty 17/14/11 skončila třikrát `AI_COMPLETED`, kanonická validace vždy prošla a workflow se nezměnilo. Starší úplná regrese nad dokumentem 1 měla inference 258 136 ms a 254 989 ms, prompt-injection 262 097 ms, 12 OK / 0 WARNING / 0 blocking a zachovala stav `EXPORT_CREATED`. Kandidáti se bez potvrzení neaplikovali a prompt injection nezměnila žádné pole.
- Stage E: allocations 700/510 Kč, tři assignments, RETURN/REJECT/REOPEN, invalidace, idempotentní/souběžné approvals, 403 a Paperless tagy prošly; skončilo `APPROVED`.
- Stage F: re-approval, XSD-valid generování/re-export, XML/PDF/ZIP hashe, response parser a bankovní XML semantics prošly; skončilo `EXPORT_CREATED`.
- Živá aktualizace: samostatná session `approver1` schválila úkol, zatímco manager session zůstala otevřená bez ručního reloadu; polling změnu zobrazil po 116 ms. Následný cleanup schválil zbývající úkoly a Stage F vytvořila aktuální export.
- Audit: mutace a stažení nesou subject, username, role, revizi a korelační ID. Ověřeny byly mimo jiné `INVOICE_FIELD_CHANGED`, `APPROVED`, `XML_GENERATION_REQUESTED`, `EXPORT_DOWNLOADED`, `PDF_DOWNLOADED` a `ZIP_DOWNLOADED`.
- Opravný smoke: vlastní ID 6/7 prošla Paperless upload → OCR 911/911 → Approval → AI COMPLETED; parser opravil `19-2000145399/0800`, duplicita byla otagována, skryta, blokována, obnovena s auditem a znovu vyřazena. Po odstranění pouze vlastních Paperless ID nastavila reconciliation `MISSING`; PDF/submit/export vrátily 409.
- Deployment proběhl přes `git pull --ff-only`, `docker compose config --quiet`, build, `up -d`, migraci a reconciliation bez mazání databází, auditů nebo volumes.

Opravná iterace přidala korelační audit stažení a mutací, ochranu stale revizí, workflow stepper, inline chyby a pending stavy, Prague timestamps a polling bez přepisování rozepsaných dat. Skutečné OIDC, Paperless, Qwen3 8B, souběžné session a POHODA exportní smoke testy prošly.
