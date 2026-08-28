# Integrace Paperless

Kompletní testovací prostředí obsahuje vlastní izolovaný Paperless-ngx 3.0.5. Má vlastní PostgreSQL databázi/uživatele, Redis a volumes `paperless_data`, `paperless_media`, `paperless_consume` a `paperless_export`. Nesmí sdílet data, tokeny ani storage s produkční instancí. Produkční architektura může později použít existující externí Paperless.

## Runtime hranice

Approval backend nikdy nepřistupuje do Paperless databáze. Používá výhradně REST API v10 na interní URL `http://paperless:8000`, ukládá unikátní `paperless_document_id` a přes API načítá OCR text, metadata a originální PDF. PDF se mimo autoritativní Paperless trvale uchovává jen v auditovatelném exportním ZIPu.

`paperless-bootstrap` je jednorázová idempotentní Paperless management úloha, nikoli approval runtime. Vytvoří skupiny `QUEUE_MANAGER`/`APPROVER`, konfigurovatelné tagy a test-only API service account. Token uloží s oprávněním 0600 do volume `paperless_api_secret`; backend/worker jej mountují read-only. Token se netiskne a není v `.env` ani Gitu.

## Tagy a synchronizace

Výchozí tagy jsou `Přijatá faktura`, `AI zpracování`, `Kontrola správce`, `Ke schválení`, `Schváleno`, `Zamítnuto`, `Připraveno pro Pohodu`, `Exportováno`, `Importováno do Pohody`, `Duplicita` a `Ignorováno`. Názvy jsou v `.env`, nikoli rozptýlené v kódu. Chybějící dokument se netaguje; není co bezpečně změnit.

Synchronizace je idempotentní: unikátní index zabrání duplicitní faktuře a beze změny snapshotu nevzniká další datový audit. Worker hledá dokumenty s `PAPERLESS_INBOX_TAG`, přes API dohledá názvy tagů a korespondenta, uloží metadata a OCR a centralizovaně přejde do `QUEUE_REVIEW`. Stav synchronizace je `PENDING`, `SYNCED` nebo `ERROR`. Klient před změnou načte dokument a nahrazuje pouze spravované stavové tagy; ostatní tagy zachová.

## OCR a persistence

Výchozí OCR je `ces+eng`; image obsahuje dodatečné balíky `ces` a `slk`. `PAPERLESS_OCR_MODE=auto` zachová použitelnou textovou vrstvu born-digital PDF a OCR provede pro obrazové dokumenty. Povinný smoke test používá image-only fixture, takže skutečně prověří OCR.

Upload probíhá přes UI nebo `/api/documents/post_document/`. Stav zpracování se sleduje přes `/api/tasks/?task_id=...`; po dokončení musí `/api/documents/{id}/` vrátit neprázdný `content` a download endpoint původní PDF.

Approval databáze ukládá OCR text, ale nikdy PDF bytes. Autorizovaný PDF proxy endpoint vždy volá Paperless download REST endpoint; browser Paperless token nezná. Etapy B/C nespouštějí Ollamu ani nevytvářejí extrakční job.

## Fulltext pro Moji historii

Endpoint dokumentů Paperless přijímá parametr `query` a stránkování. Approval backend jej používá jako jediný OCR/fulltext backend a z výsledku přebírá pouze document ID. Dotaz není prováděn jednou pro každou fakturu. Výsledná množina vzniká až průnikem s historickými assignmenty přihlášeného approvera v Approval DB; teprve poté se vrací počet, strukturovaná metadata a krátký snippet z již autorizovaného OCR snapshotu.

Paperless nemůže rozšířit oprávnění. Nepovolený výsledek se neobjeví v response, počtu, snippetu, detailu ani PDF URL. Fulltextový timeout nebo auth/5xx chyba se nepřekládá na `MISSING`.

## Upload z Approval aplikace

Běžný `QUEUE_MANAGER` se kvůli uploadu nepřihlašuje do Paperless UI. React podporuje file picker, drag & drop a více PDF; každý soubor odesílá nezávisle na `POST /api/uploads` a sleduje přes `/api/uploads/{id}`. Výchozí limit je `UPLOAD_MAX_BYTES=8388608` a musí zůstat nižší než Nginx `client_max_body_size`. Backend autoritativně vyžaduje `.pdf`, podporovaný MIME typ a `%PDF-` signaturu.

Backend řeší název pouze jako sanitizované metadata, vypočte SHA-256 a PDF po předání Paperless nedrží. Inbox tag se resolvuje názvem `PAPERLESS_INBOX_TAG`; hard-coded tag ID se nepoužívá. Multipart míří na oficiální `/api/documents/post_document/`, vrácený task UUID se sleduje přes API v10 `/api/tasks/` až k `related_document_ids`. Poté vznikne idempotentní Approval invoice a OCR/AI pokračují asynchronně. Kontrakt odpovídá [oficiálnímu Paperless REST upload postupu](https://docs.paperless-ngx.com/api/#posting-documents).

Tracking ukládá `DOCUMENT_UPLOAD_REQUESTED`, `DOCUMENT_UPLOADED_TO_PAPERLESS` nebo `DOCUMENT_UPLOAD_FAILED`, actor/username, bezpečný filename, velikost, MIME, SHA-256, correlation, task/document ID a bezpečnou chybu. Obsah PDF ani token v auditu nejsou. Stejný idempotency key a hash nevytvoří další upload; stejný hash s novým klíčem je pouze duplicate warning a nevede k mazání nebo přepsání dokumentu.

Timeout, omezený exponential backoff a job error chrání approval worker před výpadkem Paperless. Testovací service account má záměrně široký přístup jen v izolovaném tenantovi; produkční nasazení musí použít least-privileged účet.

## Reconciliation a smazaný zdroj

Discovery tag neurčuje, zda již známý dokument stále existuje. Worker proto samostatně načítá každý Approval `paperless_document_id`. HTTP 404 nastaví `MISSING`, čas prvního zjištění, audit `SOURCE_DOCUMENT_MISSING` a blocking validation. Opakovaná 404 nevytváří další stejný audit. Úspěšné načtení nastaví `AVAILABLE` a jednou zapíše `SOURCE_DOCUMENT_RESTORED`.

Timeout, DNS/network error, HTTP 5xx a auth chyba pouze nastaví diagnostický `sync_status=ERROR`; nikdy se z nich neodvozuje smazání. `MISSING` blokuje proxy PDF, nové approval rozhodnutí, nový/re-export, ZIP a potvrzení importu. Existující revize, rozhodnutí, audit a XML artifact zůstávají čitelné. Správce může orphan označit jako potvrzenou duplicitu, ale worker se jej nepokouší tagovat.
