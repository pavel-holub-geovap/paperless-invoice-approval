# Integrace Paperless

Paperless je externí autoritativní úložiště. Backend používá serverový token z `PAPERLESS_API_TOKEN`, vyhledává dokumenty s konfigurovatelným `PAPERLESS_INBOX_TAG`, ukládá unikátní `paperless_document_id` a načítá OCR/PDF na požádání.

Synchronizace je idempotentní: unikátní index a idempotency key zabrání duplicitní faktuře i jobu. Stavové tagy jsou konfigurovatelné. Změny tagů musí zachovat nesouvisející uživatelské tagy; klient nejdřív načte aktuální dokument a mění jen spravovanou množinu.

Timeout, omezený exponential backoff a čitelný job error chrání před výpadkem. PDF se neukládá trvale mimo exportní archiv.

