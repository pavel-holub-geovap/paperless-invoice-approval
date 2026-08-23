# Pokyny pro agenty

## Účel a architektura

Projekt zpracovává přijaté faktury uložené v externím Paperless. FastAPI backend a databázový worker používají PostgreSQL; Ollama provádí pouze strukturované vytěžení; Keycloak je jediný OIDC provider; React UI obsluhuje správce fronty a schvalovatele; Nginx je vstupní vrstva. POHODA XML vzniká deterministicky a importuje se ručně.

Podrobnosti jsou v `docs/ARCHITECTURE.md`. Doménová logika patří do `backend/app/services`, ne do route handlerů. Stav faktury mění jen workflow služba. Auditní záznamy jsou append-only.

## Spuštění a testy

- Kopíruj `.env.example` na `.env`; žádný secret necommituj.
- Konfigurace: `docker-compose config`
- Stack: `docker-compose up -d --build`
- Backend testy: `cd backend` a `pytest`
- Frontend testy: `cd frontend` a `npm run test`
- Frontend build: `cd frontend` a `npm run build`

## Integrační testovací server

- Připojení: `ssh ubuntudocker`
- Linux uživatel: `codex`
- Projektový adresář: `/opt/paperless-invoice-approval`
- Účel: integrační testy a testovací Docker deployment projektu

Při požadavku „Nasaď a otestuj aktuální verzi“ použij tuto VM a postup z `docs/DEPLOYMENT_ENVIRONMENT.md`. Na serveru neupravuj hlavní zdrojový kód mimo Git historii. Projektový checkout aktualizuj pouze z ověřeného správného remote pomocí `git pull --ff-only`.

K 2026-08-23 projektový adresář na VM neexistoval a uživatel `codex` nemohl zapisovat do `/opt`. Nevytvářej jej pomocí neřízeného `sudo`; vyžádej si souhlas s jednorázovým vytvořením a nastavením vlastníka `codex:codex`, případně pokyn k jinému adresáři. Aktuální inventura, síťová dostupnost a další blokátory jsou v `docs/DEPLOYMENT_ENVIRONMENT.md`.

## Git workflow

Před prací zkontroluj `git status`, remote a branch. Pokud remote existuje, použij `git fetch` a `git pull --ff-only`. Zachovej uživatelovy rozpracované změny. Nepoužívej force push ani destruktivní reset. Po logickém celku zkontroluj diff a secrets, spusť relevantní testy, aktualizuj dokumentaci a vytvoř smysluplný commit. Push prováděj pouze do správného remote.

## Bezpečnost a hranice

- Nikdy necommituj `.env`, tokeny, hesla, privátní klíče, runtime databáze, modely ani originální faktury.
- Paperless je externí. Neměň jeho konfiguraci naslepo a nikdy nevystavuj Paperless token browseru.
- Originální PDF trvale neduplikuj; výjimkou je archivovaný exportní balíček.
- Do POHODY se systém přímo nepřipojuje, nezapisuje do její databáze a neprovádí automatický import.
- Akce, která by mohla změnit externí Paperless, poškodit data, zveřejnit secret nebo přímo zapsat do POHODY, vyžaduje předem potvrzení uživatele.

## Workflow invarianty

1. Před schvalováním musí být zkontrolován originál, validace nesmí obsahovat blokující chybu a součet allocations musí sedět.
2. Approval assignment náleží allocation a revizi; všechny povinné assignments musí schválit.
3. Významná změna dat, allocations nebo schvalovatelů vytvoří novou revizi a invaliduje dřívější schválení, ale nemaže je.
4. `RETURN` a `REJECT` vyžadují komentář; `REJECT` platí pro celou fakturu.
5. Export je povolen jen po finálním schválení a úspěšné XSD validaci.
6. `EXPORT_CREATED` není `IMPORTED_TO_POHODA`; druhý stav vzniká jen explicitním potvrzením správce.
