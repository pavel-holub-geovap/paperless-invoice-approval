# Integrace Paperless

Kompletní testovací prostředí obsahuje vlastní izolovaný Paperless-ngx 3.0.5. Má vlastní PostgreSQL databázi/uživatele, Redis a volumes `paperless_data`, `paperless_media`, `paperless_consume` a `paperless_export`. Nesmí sdílet data, tokeny ani storage s produkční instancí. Produkční architektura může později použít existující externí Paperless.

## Runtime hranice

Approval backend nikdy nepřistupuje do Paperless databáze. Používá výhradně REST API v10 na interní URL `http://paperless:8000`, ukládá unikátní `paperless_document_id` a přes API načítá OCR text, metadata a originální PDF. PDF se mimo autoritativní Paperless trvale uchovává jen v auditovatelném exportním ZIPu.

`paperless-bootstrap` je jednorázová idempotentní Paperless management úloha, nikoli approval runtime. Vytvoří skupiny `QUEUE_MANAGER`/`APPROVER`, konfigurovatelné tagy a test-only API service account. Token uloží s oprávněním 0600 do volume `paperless_api_secret`; backend/worker jej mountují read-only. Token se netiskne a není v `.env` ani Gitu.

## Tagy a synchronizace

Výchozí tagy jsou `Přijatá faktura`, `AI zpracování`, `Kontrola správce`, `Ke schválení`, `Schváleno`, `Zamítnuto`, `Připraveno pro Pohodu`, `Exportováno` a `Importováno do Pohody`. Názvy jsou v `.env`, nikoli rozptýlené v kódu.

Synchronizace je idempotentní: unikátní index a idempotency key zabrání duplicitní faktuře i jobu. Klient před změnou načte dokument a nahrazuje pouze spravované stavové tagy; ostatní tagy zachová.

## OCR a persistence

Výchozí OCR je `ces+eng`; image obsahuje dodatečné balíky `ces` a `slk`. `PAPERLESS_OCR_MODE=auto` zachová použitelnou textovou vrstvu born-digital PDF a OCR provede pro obrazové dokumenty. Povinný smoke test používá image-only fixture, takže skutečně prověří OCR.

Upload probíhá přes UI nebo `/api/documents/post_document/`. Stav zpracování se sleduje přes `/api/tasks/?task_id=...`; po dokončení musí `/api/documents/{id}/` vrátit neprázdný `content` a download endpoint původní PDF.

Timeout, omezený exponential backoff a job error chrání approval worker před výpadkem Paperless. Testovací service account má záměrně široký přístup jen v izolovaném tenantovi; produkční nasazení musí použít least-privileged účet.
