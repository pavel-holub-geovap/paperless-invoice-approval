# Aktuální stav

- Datum ověření: 2026-08-23
- Branch: `main`
- Git remote: `git@github-paperless-approval:pavel-holub-geovap/paperless-invoice-approval.git`
- Approval aplikace: `http://172.30.172.167/`
- Nasazeno: PostgreSQL, Redis, Keycloak, Paperless-ngx, Nginx, `approval-backend`, `approval-worker`, `approval-frontend`, Ollama a jednorázový `ollama-pull`. Všech devět dlouhodobých služeb je healthy; `keycloak-provision`, `paperless-bootstrap` a `ollama-pull` skončily kódem 0.
- Databáze: Approval používá vlastní databázi a credentials. Alembic je na `0004 (head)`. Backend ani worker nemají Paperless DB credentials a komunikují s Paperless pouze přes REST API.
- Testovací dokument: Paperless `paperless_document_id=1`, `synthetic-invoice-cs-en.pdf`, OCR 911 znaků. Originální PDF není trvale uloženo v Approval DB; chráněný proxy endpoint vrátil HTTP 200, `application/pdf` a 92 182 B.
- OIDC: skutečný Authorization Code flow prošel pro `queue-manager`, `approver1`, `approver2` a `approver3`. Role jsou vynucené backendem; approver nemůže otevřít cizí assignment ani manažerský seznam (HTTP 403).

## Etapa E

- Etapa E je implementována a ověřena přes nasazený systém s reálnými Keycloak uživateli.
- Střediska jsou databázová entita s auditovaným CRUD. V testovací DB je 6 zachovaných záznamů; scénář používá `200 – Vývoj` a `300 – Obchod`.
- Aktuální revize testovací faktury je 22. Rozúčtování je 700,00 Kč na středisko 200 a 510,00 Kč na středisko 300; součet 1 210,00 Kč přesně odpovídá celkové částce faktury a zbývá 0,00 Kč.
- Aktivní assignmenty aktuální revize jsou `approver1 / 200 / 700,00 Kč`, `approver2 / 300 / 510,00 Kč` a `approver3 / 300 / 510,00 Kč`. Všechny tři skončily `APPROVED` a v DB existují právě tři platná rozhodnutí.
- Povinné potvrzení kontroly originálu je u aktuální revize aktivní. Backend při submitu znovu kontroluje originál, blokující validace, součet allocations, approvery a jejich aktivní roli.
- Reálná sekvence byla `AWAITING_APPROVAL → AWAITING_APPROVAL → AWAITING_APPROVAL → APPROVED`; první ani druhé schválení fakturu předčasně neuzavřelo.
- Prošly negativní scénáře: nesedící allocations, allocation bez approvera, nepotvrzený originál, blokující validace, cizí assignment, chybějící komentář u RETURN/REJECT, APPROVE po REJECT a přístup approvera k manažerskému API.
- Prošly RETURN (`RETURNED`), REJECT (`REJECTED`) a REOPEN (`NEEDS_REVIEW`) se zachováním historie. Audit obsahuje nové doménové události `RETURNED`, `REJECTED` a `REOPENED`.
- Změna částky, střediska, approvera i fakturačního údaje po schválení vytvořila novou revizi a událost `APPROVAL_INVALIDATED`. Historicky invalidovaných rozhodnutí je 21; nebyla smazána.
- Dvě souběžná APPROVE na různých assignmentech prošla. Opakovaný stejný APPROVE vrátil stejné rozhodnutí a nevytvořil duplicitní platný záznam.
- Paperless tagy byly potvrzeny následným GET přes REST API: RETURNED → `Kontrola správce`, REJECTED → `Zamítnuto`, APPROVED → `Schváleno`. Všech 40 workflow synchronizačních jobů je `DONE`; žádný není pending, running ani failed.
- Frontend obsahuje dashboard a filtry správce, správu středisek, rozúčtování bez reloadu, potvrzení originálu, schvalovatele, stav schválení a audit. „Moje úkoly“ zobrazuje schvalovateli pouze aktivní vlastní assignmenty s PDF, revizí, střediskem a částkou.

## Etapa D

- Ollama: `ollama/ollama:0.32.14`, model `qwen3:4b`, CPU režim, jedna paralelní inference, kontext 4096, teplota 0 a timeout 300 s.
- Striktní kontrakt je `invoice-extraction.v1`; prompt `invoice-extraction.cs-en.v1`. OCR je oddělené jako nedůvěryhodný datový blok a výsledek prochází JSON a Pydantic validací.
- Golden smoke porovnal 21 hodnot: 18 správně, 2 chybně (`bank_account`, `description`) a 1 chybějící (`bank_code`). Etapa E proto přidala deterministickou validaci domácího účtu, IBAN a BIC/SWIFT.
- Prompt-injection test nezměnil cílová pole útoku a bezpečná re-extrakce nepřepsala aplikovaná data ani workflow.
- V DB zůstává append-only historie 5 dokončených a 1 neúspěšného AI jobu z Etapy D. Neúspěšný AI job nesouvisí s workflow ani Paperless tag synchronizací.

## Závěrečné ověření

- Backend: Ruff čistý; 53/53 testů prošlo.
- Frontend: 3 testovací soubory, 5/5 testů; TypeScript kontrola a produkční Vite build prošly.
- Deployment: `git pull --ff-only`, `docker compose config --quiet`, build, `up -d`, migrace a `docker compose ps -a` prošly bez mazání databází nebo volumes.
- Regresní Stage B smoke prošel: OIDC, role, Paperless dokument, OCR, databázový snapshot a originální PDF.
- Plný Stage E smoke prošel: všechny kladné i povinné záporné scénáře, concurrency, idempotence a potvrzené Paperless tagy.
- Během prvních běhů smoke test odhalil stale ORM response a neobnovený snapshot Paperless tagu. Obě závady byly opraveny a kryty testem nebo tvrdou smoke podmínkou; závěrečný běh nemá chyby.
- Po závěrečném dokumentačním commitu musí být lokální `main`, `origin/main` a VM checkout `/home/codex/paperless-invoice-approval` na shodném hashi a čisté.
- POHODA XML, XSD validace a export nejsou součástí Etapy E a nebyly měněny.
