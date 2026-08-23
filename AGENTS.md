# Pokyny pro agenty

## Účel a architektura

Projekt zpracovává přijaté faktury uložené v Paperless. Kompletní testovací stack obsahuje izolovaný Paperless-ngx, PostgreSQL, Redis, Keycloak, Ollama, FastAPI backend, worker, React UI a Nginx. Produkční varianta může později použít existující externí Paperless. POHODA XML vzniká deterministicky a importuje se ručně.

Podrobnosti jsou v `docs/ARCHITECTURE.md`. Doménová logika patří do `backend/app/services`, ne do route handlerů. Stav faktury mění jen workflow služba. Auditní záznamy jsou append-only.

## Spuštění a testy

- Kopíruj `.env.example` na `.env`; žádný secret necommituj.
- Konfigurace: `docker-compose config`
- Stack Etapy F: `docker-compose up -d --build` (včetně Ollamy a idempotentního stažení modelu)
- Backend testy: `cd backend` a `pytest`
- Frontend testy: `cd frontend` a `npm run test`
- Frontend build: `cd frontend` a `npm run build`

## Integrační testovací server

- Připojení: `ssh ubuntudocker`
- Linux uživatel: `codex`
- Projektový adresář: `/home/codex/paperless-invoice-approval`
- Účel: integrační testy a testovací Docker deployment projektu
- Testovací prostředí obsahuje: Paperless, Keycloak, PostgreSQL, Redis, CPU-only Ollamu, approval backend/worker/frontend a reverse proxy.

Při požadavku „Nasaď a otestuj aktuální verzi“ použij tuto VM a postup z `docs/DEPLOYMENT_ENVIRONMENT.md`. Na serveru neupravuj hlavní zdrojový kód mimo Git historii. Projektový checkout aktualizuj pouze z ověřeného správného remote pomocí `git pull --ff-only`.

Nepoužívej původně plánovaný `/opt`; běžný deployment nesmí vyžadovat administrátorský zásah. Aktuální inventura, síťová dostupnost a blokátory jsou v `docs/DEPLOYMENT_ENVIRONMENT.md`.

## Git workflow

Před prací zkontroluj `git status`, remote a branch. Pokud remote existuje, použij `git fetch` a `git pull --ff-only`. Zachovej uživatelovy rozpracované změny. Nepoužívej force push ani destruktivní reset. Po logickém celku zkontroluj diff a secrets, spusť relevantní testy, aktualizuj dokumentaci a vytvoř smysluplný commit. Push prováděj pouze do správného remote.

## Bezpečnost a hranice

- Nikdy necommituj `.env`, tokeny, hesla, privátní klíče, runtime databáze, modely ani originální faktury.
- Testovací Paperless je izolovaná součást tohoto stacku. Nikdy jej nepropojuj s produkční databází, storage nebo tokenem a nikdy nevystavuj Paperless token browseru.
- Originální PDF trvale neduplikuj; výjimkou je archivovaný exportní balíček.
- Approval worker načítá dokumenty pouze přes Paperless REST API. LLM smí převádět OCR jen na striktní `InvoiceExtractionV1`; nesmí generovat XML/SQL, měnit workflow ani určovat střediska či schvalovatele.
- Každý AI běh je append-only. Re-extrakce je pouze kandidát a pracovní data smí přepsat až po explicitním potvrzení správce.
- Do POHODY se systém přímo nepřipojuje, nezapisuje do její databáze a neprovádí automatický import.
- LLM nikdy negeneruje POHODA XML. XML vytváří pouze deterministický generátor ze schváleného immutable snapshotu a před zpřístupněním musí projít verzovaným XSD bundle.
- POHODA response XML je pouze diagnostické; jeho upload nesmí automaticky měnit workflow stav.
- Akce, která by mohla změnit jiný než izolovaný testovací Paperless, poškodit persistentní data, zveřejnit secret nebo přímo zapsat do POHODY, vyžaduje předem potvrzení uživatele.
- Bez výslovného souhlasu nepoužívej `docker compose down -v`, `docker system prune`, `docker volume prune`, nemaž databáze ani Paperless storage.

## Workflow invarianty

1. Před schvalováním musí být zkontrolován originál, validace nesmí obsahovat blokující chybu a součet allocations musí sedět.
2. Approval assignment náleží allocation a revizi; všechny povinné assignments musí schválit.
3. Významná změna dat, allocations nebo schvalovatelů vytvoří novou revizi a invaliduje dřívější schválení, ale nemaže je.
4. `RETURN` a `REJECT` vyžadují komentář; `REJECT` platí pro celou fakturu.
5. Rozhodovací transakce zamyká fakturu a assignment; jeden assignment smí mít nejvýše jedno platné rozhodnutí.
6. Export je povolen jen po finálním schválení a úspěšné XSD validaci.
7. `EXPORT_CREATED` není `IMPORTED_TO_POHODA`; druhý stav vzniká jen explicitním potvrzením správce.
8. Exportní artifact náleží konkrétní revizi; XML, snapshot a hash jsou immutable. Změněná revize vyžaduje nové schválení, nikoli re-export.
9. Do XML se exportují allocations, nikoli approval assignmenty. Více středisek a více sazeb vyžaduje explicitní schválené `Allocation.vat_breakdown`.
