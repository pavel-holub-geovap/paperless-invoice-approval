# Aktuální stav

- Datum ověření: 2026-08-23
- Branch: `main`
- Git remote: `git@github-paperless-approval:pavel-holub-geovap/paperless-invoice-approval.git`. Po závěrečném dokumentačním commitu musí být lokální `main`, `origin/main` a checkout `/home/codex/paperless-invoice-approval` na shodném hashi.
- Nasazeno: PostgreSQL, Redis, Keycloak, Paperless-ngx, Nginx, `approval-backend`, `approval-worker`, `approval-frontend`, Ollama a jednorázový `ollama-pull`. Všech devět dlouhodobých služeb je healthy; `keycloak-provision`, `paperless-bootstrap` a `ollama-pull` skončily kódem 0.
- Databáze: Approval používá vlastní databázi a credentials. Alembic je na `0003 (head)`. Backend ani worker nemají Paperless DB credentials a komunikují s Paperless pouze přes REST API.
- Testovací dokument: Paperless `paperless_document_id=1`, `synthetic-invoice-cs-en.pdf`, název `Synthetic Invoice CS-EN – integration smoke test`, OCR 911 znaků. Stav je `SYNCED`, workflow `QUEUE_REVIEW` a AI stav `AI_COMPLETED`.
- Originální PDF: není trvale uloženo v Approval DB. Chráněný proxy endpoint vrátil HTTP 200, `application/pdf` a 92 182 B.
- OIDC a role: skutečný Authorization Code flow prošel pro `queue-manager` s `QUEUE_MANAGER` i `approver1` s `APPROVER`. Queue manager vidí jednu fakturu; approver má 0 úkolů a manažerský seznam mu vrací HTTP 403.
- UI: Approval běží na `http://172.30.172.167/`. Detail zobrazuje AI stav, model, verzi schématu a promptu, dobu inference, historii revizí, provenance, deterministické validace a bezpečnou akci pro použití kandidátní re-extrakce; originál zůstává vlevo přes PDF proxy.

## Etapa D

- Ollama: `ollama/ollama:0.32.14`, model `qwen3:4b` (2,5 GB), čistě CPU (`OLLAMA_NUM_GPU=0`), jedna paralelní inference, kontext 4096, teplota 0, `keep_alive=5m`, timeout 300 s.
- Striktní kontrakt: `invoice-extraction.v1`; prompt `invoice-extraction.cs-en.v1`; odpověď prochází `json.loads` a Pydantic validací. OCR je oddělené jako nedůvěryhodný datový blok.
- Produkční historie: revize 1 je append-only `AI_FAILED` po prvotním nekompatibilním grammar požadavku. Revize 2 je `AI_COMPLETED`, trvala 143 856 ms a jako první úspěšná byla aplikována. Revize 3–6 jsou dokončené, neaplikované kandidáty vyžadující explicitní potvrzení; poslední trvala 151 288 ms.
- Finální golden smoke: 21 porovnávaných hodnot, 18 správně, 2 chybně (`bank_account`, `description`) a 1 chybějící (`bank_code`). Deterministická validace: 11 OK, 1 warning, 0 blocking errors.
- Prompt injection: reálná oddělená inference prošla. Nezměnila žádné cílové pole útoku (`supplier_name`, `total_amount`), nevygenerovala požadovaný škodlivý název ani XML. Oproti baseline se změnil pouze volný `description`; napadený výstup měl golden skóre 19/21, s chybným `bank_account` a chybějícím `bank_code`.
- Bezpečná re-extrakce: potvrzeno, že nová revize nepřepsala aplikovaná data a nezměnila business workflow stav.
- Audit: zachovává objevení a synchronizaci dokumentu, workflow přechody, queue/start/retry/fail/success/apply události AI i přihlášení uživatelů. V DB je 1 neúspěšný a 5 dokončených AI jobů; neúspěšná historie nebyla smazána.

## Provozní měření

- Před Ollamou: 11 GiB RAM celkem, přibližně 1,7 GiB použito a 9,6 GiB available.
- Během inference: přibližně 5,0 GiB použito, 6,4 GiB available, Ollama 3,287 GiB a přibližně 296 % CPU.
- Po smoke testu: 4,8 GiB použito, 6,5 GiB available, swap 6 MiB; Ollama drží model v paměti a používá 3,285 GiB z limitu 3,516 GiB.
- CPU inference má na této VM latenci přibližně 2,3–3,3 minuty. Je funkční a bounded, ale pro vyšší provozní propustnost bude potřeba rychlejší CPU/GPU nebo menší model.

## Ověření a známé chyby

- Testy: Ruff čistý; 40 backendových testů; frontend 2 testovací soubory / 3 testy; TypeScript lint a Vite production build; `docker compose config`; Stage B regresní smoke; reálný Stage D smoke.
- Při nasazení neexistoval původně zvolený image tag `0.32.7`; byl nahrazen dostupným `0.32.14`.
- Pydantic JSON Schema nebylo možné předat přímo grammar compileru této verze Ollamy (`HTTP 400`), proto se používá Ollama JSON mode, celý kontrakt v system promptu a následná striktní Pydantic boundary validace.
- První CPU inference překročila původních 180 s; bounded timeout byl nastaven na 300 s. Retry a append-only audit chybu korektně zachovaly.
- Nejsou blokátory pro Etapu D. POHODA export, schvalovací assignments a další etapy nejsou součástí tohoto stavu.
