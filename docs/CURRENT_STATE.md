# Aktuální stav

- Datum ověření: 2026-08-23
- Branch: `main`
- Git remote: `git@github-paperless-approval:pavel-holub-geovap/paperless-invoice-approval.git`
- Approval aplikace: `http://172.30.172.167/`
- Nasazeno: PostgreSQL, Redis, Keycloak, Paperless-ngx, Nginx, `approval-backend`, `approval-worker`, `approval-frontend`, Ollama a jednorázový `ollama-pull`. Všechny dlouhodobé služby jsou healthy; `keycloak-provision`, `paperless-bootstrap` a `ollama-pull` skončily kódem 0.
- Databáze: Approval používá vlastní databázi a credentials. Alembic je na `0005 (head)`. Backend ani worker nemají Paperless DB credentials a komunikují s Paperless pouze přes REST API.
- Testovací dokument: Paperless `paperless_document_id=1`, `synthetic-invoice-cs-en.pdf`, OCR 911 znaků. Originální PDF není uloženo v Approval DB; chráněný proxy endpoint vrátil HTTP 200, `application/pdf` a 92 182 B.
- OIDC: skutečný Authorization Code flow prošel pro `queue-manager`, `approver1`, `approver2` a `approver3`. Role jsou vynucené backendem; approver nemůže otevřít cizí assignment ani manažerský seznam (HTTP 403).

## Etapa F

- Aktivní oficiální POHODA XML 2.x bundle je `schemas/pohoda/2025-10-16`, stažený 23. 8. 2026 z kompletního STORMWARE archivu. Obsahuje 75 XSD, aktivní root je `data.xsd` a bundle SHA-256 je `ab6a9f3c406a9e2257f544203d21df3723e8e10026e73a0898aa6249446bfd9b`.
- Generátor `pohoda-received-invoice.v1` vytváří pouze `receivedInvoice` jako Windows-1250. Dodavatelské číslo používá `originalDocument`; interní číslo POHODY se negeneruje. Schválená strukturovaná adresa se exportuje přes `typ:address linkToAddress="false"` a nespojuje se automaticky s Adresářem POHODY.
- Aktuální revize syntetické faktury je 34. Její data, originál, allocations i approvery byly po opravě strukturované adresy a dodavatelského čísla znovu zkontrolovány a schváleny.
- Rozúčtování je 700,00 Kč na `pohoda_code=200` a 510,00 Kč na `pohoda_code=300`. Vznikly přesně dvě účetní položky, nikoli tři assignmentové položky: `578,51 + 121,49 = 700,00` a `421,49 + 88,51 = 510,00`. Celkový základ 1 000,00 Kč, DPH 210,00 Kč a total 1 210,00 Kč jsou zachovány.
- Konkrétní aktuální artifact je `c40c9843-67f1-4acd-be98-889bdf418da0`, navázaný re-exportem na `91ecaf96-4714-400d-beb1-2f43503319c7`. XML má 3 065 B, SHA-256 `6d7cea0a32d7d2cd3ff944588dbfdf4a6b0e422b012aaabe06e398d7dad96e2b` a stav `XSD_VALID`.
- Originální PDF má 92 182 B a SHA-256 `0e19003f1467172a88d4735405d7dd45db84dcd6560904ba553128d862483838`. Hash při tvorbě XML a batch ZIPu souhlasil.
- Batch `EXP-2026-000001` (`859225ed-08ce-4b09-9104-2acbf4e943e5`) má SHA-256 `c5a1da8d0e5da2b2891f856a1b16abf3eed0a8d042ee33a09ea6817f640a5120` a stabilní položky `invoice-TEST-2026-0001/invoice.xml` a `invoice.pdf`.
- XML lze po přihlášení stáhnout z detailu faktury nebo endpointu `/api/exports/artifacts/c40c9843-67f1-4acd-be98-889bdf418da0/xml`. Workflow je `EXPORT_CREATED`; import do POHODY nebyl potvrzen.
- Reálný negativní běh zachytil příliš dlouhé `originalDocument` jako `XSD_INVALID` s line/column/path a nepovolil přechod do ready stavu. Po auditované opravě a novém schválení prošel XSD-validní re-export.
- Oficiální response fixture se načetla jako `PARSED`; upload vytvořil audit, ale nezměnil workflow stav. Praktickou kompatibilitu ještě musí potvrdit ruční import tohoto konkrétního XML, skutečné POHODA response a kontrolní export.

## Regrese Etap B–E

- Stage B: OIDC, role, Paperless REST snapshot, OCR 911 znaků, originální PDF a zákaz manažerské fronty pro approvera prošly.
- Stage D: skutečná CPU inference `qwen3:4b` prošla. Obchodní stav zůstal `APPROVED → APPROVED`, re-extrakce zůstala neaplikovaným kandidátem a prompt-injection kontrola nezměnila cílová pole.
- Stage E: přesné allocations 700/510 Kč, tři assignments, submit preconditions, RETURN, REJECT, REOPEN, čtyři druhy invalidace, idempotentní i souběžná approvals, negativní autorizace a Paperless tagy prošly. Závěrečná approval sekvence skončila `APPROVED` před Stage F revizí.

## Závěrečné automatické ověření

- Backend: Ruff čistý; 66/66 testů prošlo.
- Frontend: 4 testovací soubory, 7/7 testů; TypeScript kontrola a produkční Vite build prošly.
- POHODA doménové testy zahrnují pět variant DPH/allocations, českou diakritiku, semantický golden XML, pět negativních XSD mutací a response parser.
- Deployment: `git pull --ff-only`, `docker compose config --quiet`, build, `up -d`, migrace a `docker compose ps -a` prošly bez mazání databází nebo volumes.
- Smoke: Stage B, opravený Stage D, plný Stage E a Stage F prošly. Stage F vytvořil konkrétní XML/PDF/ZIP artifact a ponechal jej připravený k ručnímu importu.
- Po závěrečném dokumentačním commitu musí být lokální `main`, `origin/main` a VM checkout `/home/codex/paperless-invoice-approval` na shodném hashi a čisté.
