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

Exportní hranice je jednosměrná a offline. `RECEIVED_INVOICE` s validním ISDOC používá `PDF_ISDOC`: ručně se předá schválená PDF kopie s byte-for-byte zachovanou přílohou. Bez validního ISDOC používá `GENERATED_XML`; deterministický DOM se serializuje jako Windows-1250 a validuje lokálním POHODA XSD bundle. Zálohové faktury a ostatní typy mají metodu `NONE`.

`ExportArtifact` uchovává revizi, vstupní snapshot, verze, XSD výsledek, cestu/hash/velikost XML a hash aktuálního Paperless PDF. Samotné PDF zůstává v Paperless. `ExportBatch` archivuje stabilní ZIP s `invoice-<safe-number>/invoice.xml` a `invoice.pdf` a vlastním SHA-256. Re-export stejné revize vytvoří nový artifact s vazbou na předchozí; změna revize invaliduje použitelnost starého artifactu a vrací workflow ke schválení.

Approval allocations nejsou finální účetní rozúčtování. XML položky vznikají ze skutečných vytěžených invoice items, nebo z bezpečných DPH souhrnů bez `centre`; allocations jsou jen v deterministické informativní poznámce. Diagnostický response parser stav nemění a `IMPORTED_TO_POHODA` vždy vyžaduje potvrzení správce.

## Klasifikace, ISDOC a schválená kopie

Workflow status zůstává samostatný od `document_type`, `processing_mode`, `extraction_source`, `isdoc_status`, `approved_pdf_status` a `pohoda_import_method`. Nový dokument začíná `UNCLASSIFIED`; manager volí typ a režim. `FOR_APPROVAL`, `RECORD_ONLY` a `CENTRAL_MANUAL` jsou ortogonální k typu dokladu.

Worker před AI stáhne originální PDF přes Paperless REST, ověří jeho hash a bezpečně enumeruje attachments. Podporovaný validační profil je ISDOC Invoice 6.0.2, namespace `http://isdoc.cz/namespace/2013`; po XML safety kontrole a detekci verze proběhne lokální validace proti verzovanému oficiálnímu XSD bundle, mapování, normalizace a teprve potom semantic required-field validace. Mapper je namespace-aware a používá jen explicitní strukturální cesty. Číslo dokladu je root `Invoice/ID`, IČO dodavatele je `AccountingSupplierParty/Party/PartyIdentification/ID` a `InvoiceLine/ID` je pouze identita položky; globální `.//ID` se nepoužívá. DTD/entity/network resolving jsou zakázány, velikost je omezená a více platných kandidátů je odmítnuto. Validní snapshot je immutable s přesnou provenance `source/path/raw_value`; jinak následuje OCR/AI fallback.

Opakovaná ISDOC inspekce se spouští auditovaným manager endpointem a worker znovu čte stejné originální PDF z Paperless. Pokud aktuální revize obsahuje OCR/AI data, validní ISDOC vytvoří standardní novou invoice revision; staré AI extraction, revize, audit a artifacts se nemažou.

Finální approval zařadí idempotentní `CREATE_APPROVED_PDF`. Kopie vždy rozšíří poslední MediaBox/CropBox dolů, původní obsah neposouvá ani nepřekrývá a přidá lidsky čitelné approvals/allocations. Před a po se porovnává manifest všech attachments; odlišný hash generování zablokuje. Artifact patří revizi, approval snapshotu a verzi razítka. Nová revize označí starý artifact `HISTORICAL`.

## Paperless snapshot

Approval databáze ukládá pouze `paperless_document_id`, název, čas vytvoření, korespondenta, tagy, OCR text, původní název souboru a diagnostiku synchronizace. PDF zůstává autoritativně v Paperless a endpoint `/api/invoices/{id}/pdf` jej streamuje přes REST API bez trvalé kopie. Nový dokument prochází centralizovaně `NEW → VALIDATION → QUEUE_REVIEW`; AI mezitím používá samostatné stavy `AI_PENDING → AI_PROCESSING → AI_COMPLETED|AI_FAILED`, takže nemění obchodní stav.

Worker při každém discovery cyklu navíc reconciliuje všechny uložené `paperless_document_id`. Jen explicitní REST HTTP 404 mění `source_status` z `AVAILABLE` na `MISSING`; 401/403, timeout, síťová chyba a 5xx ponechávají dostupnost beze změny. Opětovné nalezení dokumentu nastaví `AVAILABLE`. Oba přechody jsou idempotentní a auditované. `MISSING` přidává blocking validation, ale nepřepisuje workflow, revize, approvals ani exportní historii.

## Historie a fulltext schvalovatele

Historické oprávnění je odvozeno výhradně z append-only `ApprovalAssignment` napříč všemi `InvoiceRevision`, nikoliv z aktuálního workflow nebo aktivního assignmentu. Centrální `user_can_access_invoice_history(subject, invoice_id)` je bezpečnostní hranice pro historický detail a originální PDF. Seznam používá stejný existenční predikát přímo v SQL, aby se autorizace neprováděla až ve frontendu.

Fulltext má dva nezávislé zdroje s odlišnou autoritou: Approval DB poskytuje business oprávnění a strukturovaná pole, Paperless REST poskytuje OCR vyhledání a originální dokument. Backend provede nejvýše jeden paginovaný Paperless search tok pro dotaz, získané document ID protne s historicky povolenými fakturami a teprve potom vytvoří počet, metadata a případný snippet. Paperless výsledek nikdy nerozšiřuje oprávnění a browser nezná Paperless token ani globální výsledky.

Historický list je jedna faktura na řádek a používá dávkové načtení assignmentů, allocations, středisek a decisions. Indexy `(approver_subject, invoice_id)` a `(assignment_id, created_at)` podporují autorizaci, řazení i detail bez N+1.

`Invoice.disposition` je třetí, samostatná osa: `ACTIVE`, `IGNORED_DUPLICATE`, `IGNORED_OTHER`. Uchovává důvod, komentář, aktéra, čas a volitelný odkaz na původní fakturu. Výchozí fronta zobrazuje jen `ACTIVE + AVAILABLE`; další pohledy explicitně ukazují ignorované a chybějící zdroje.

### Approval upload orchestrace

Upload z browseru končí výhradně na `POST /api/uploads`; `QUEUE_MANAGER` i `APPROVER` s platným CSRF používají stejný endpoint a bezpečnostní pipeline. Backend streamově ověří limit a PDF signaturu, spočítá SHA-256, sanitizuje display filename a do `document_uploads` uloží metadata, stabilní subject, roli/origin, correlation a idempotency key, nikoli PDF bytes. Následně jedním multipart požadavkem volá oficiální Paperless `/api/documents/post_document/` s ID konfigurovaného inbox tagu.

Paperless vrátí task UUID dříve než document ID. Worker proto sleduje `/api/tasks/?task_id=...`; po získání `related_document_ids` idempotentně vytvoří jeden Invoice, stáhne snapshot přes REST a standardní cesta spustí OCR-dependent AI job. Tracking API odvozuje uživatelské stavy Paperless/OCR/AI/workflow a React je polluje po 3 s. Connect failure před odesláním dovoluje retry stejného souboru se stejným idempotency key; timeout, přerušená odpověď a 5xx jsou `SUBMISSION_UNKNOWN`, protože automatické opakování by mohlo vytvořit druhý Paperless dokument.

## AI extrakce a validace

Worker před každým během znovu načte OCR z Paperless REST API. Ollama dostane nedůvěryhodný OCR text oddělený značkami a constrained `InvoiceExtractionRawV1` JSON schema. Tok je výslovně `RawV1 → konzervativní normalizace → InvoiceExtractionV1 → účetní validace`; kanonický model se kvůli odchylkám LLM neuvolňuje. Pydantic odmítne chybějící nebo neznámá pole; hodnoty mohou být explicitně `null`. Každý běh ukládá model, schema/prompt verzi, všechny raw pokusy, přesné schema errors, normalizační mapu, parsed JSON, provenance, dobu, chybu a validační snapshot do `ai_extractions`. První běh lze aplikovat automaticky jen na prázdnou revizi; re-extrakce zůstane kandidátem do explicitního potvrzení.

Deterministická validační služba používá `Decimal`, český checksum IČO, formát DIČ/VS, ISO datum a měnu, matematiku DPH a součtů, účet, IBAN mod-97 a BIC. Před normalizací navíc sváže české označené datumové řádky OCR s jejich vlastními poli a provenance; DUZP bez explicitního popisku zůstává `null`. LLM nevytváří XML/SQL, workflow stav, cost center ani approvera. Detailní kontrakt je v `docs/AI_EXTRACTION.md`.

Backend, databáze, API a POHODA XML používají ISO datum. React má jedinou prezentační vrstvu `formatDateCs`/`formatDateTimeCs` a kontrolovaný `CzechDateInput`, takže uživatel datum vidí a zadává jako `DD.MM.YYYY`; před odesláním se validní kalendářní datum převádí zpět na ISO.

Český domácí účet prochází jedinou normalizační službou. Kombinovaný vstup `[prefix-]account/bank_code` se rozloží bez hádání číslic; původní text zůstane v `bank_account_raw`. Dodavatelská adresa se strukturuje primárně modelem a konzervativně normalizuje pouze z dodavatelského adresního bloku, nikdy z celého OCR. Volitelný modulo-11 checksum i matematické VAT reconciliation kontroly včetně řádku jsou review WARNING; pouze neplatný/neúplný VAT formát zůstává blocking. `ROUNDING` je deterministicky odvozen výhradně z explicitního řádkového štítku v evidence, nikdy ze souhrnné částky, velikosti rozdílu nebo samotné klasifikace LLM. Deklarované částky se výpočtem nepřepisují.

`QUEUE_MANAGER` vidí celou frontu a smí provádět správcovské změny. `APPROVER` vidí svoje aktuální i historické assignmenty a také dokumenty, které sám nahrál, a to už před vznikem assignmentu. Cizí approver bez uploader/assignment vztahu metadata ani PDF nevidí. Role pocházejí z Keycloak tokenu a backend je kontroluje nezávisle na viditelnosti prvků ve frontendu.

### Sekce, self-approval a kontrola revize

Business „Sekce“ používá stávající `CostCenter`; nevzniká druhý překrývající se číselník. `ApproverSectionPermission` je auditovatelná M:N vazba stabilního Keycloak subjectu na sekci. Approver smí vlastní allocations vytvořit pouze pro aktivní povolené sekce a pro každý takový řádek dostane standardní assignment. Každé nové rozhodnutí znovu ověřuje aktuální permission.

Self-approval je běžný append-only `ApprovalDecision`, ale před queue review nemění dokument na `APPROVED` ani nevytváří schválené PDF. `submitted_to_queue_*` a `queue_manager_reviewed_*` jsou uloženy na `InvoiceRevision`; fork revize tedy review automaticky zneplatní. Správcovská změna klasifikace, režimu, sekcí nebo approverů po předání vytvoří novou revizi, historická rozhodnutí pouze invaliduje a nemaže.

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
