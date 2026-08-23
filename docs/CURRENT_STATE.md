# Aktuální stav

- Datum: 2026-08-23
- Branch: `main`
- Git remote: `git@github-paperless-approval:pavel-holub-geovap/paperless-invoice-approval.git`; lokální `main`, `origin/main` a checkout `/home/codex/paperless-invoice-approval` jsou synchronizované.
- Nasazeno: PostgreSQL, Redis, Keycloak, Paperless-ngx, Nginx, `approval-backend`, `approval-worker` a `approval-frontend`. Trvalé služby jsou healthy; `keycloak-provision` a `paperless-bootstrap` skončily kódem 0.
- Databáze: Approval používá vlastní databázi a credentials. Alembic je na `0002 (head)`. Backend ani worker nemají Paperless DB credentials a používají jen Paperless REST API token z read-only runtime volume.
- Testovací dokument: Paperless `paperless_document_id=1`, `synthetic-invoice-cs-en.pdf`, název `Synthetic Invoice CS-EN – integration smoke test`, bez korespondenta, OCR 911 znaků.
- Synchronizace: `SYNCED`, workflow `QUEUE_REVIEW`, stavový Paperless tag byl změněn workerem přes REST API. Audit obsahuje objevení, synchronizaci a přechody `NEW → VALIDATION → QUEUE_REVIEW`. Originální PDF není v Approval DB; chráněný proxy endpoint vrátil `application/pdf`, 92 182 B.
- OIDC a role: skutečný Authorization Code flow prošel pro `queue-manager` s `QUEUE_MANAGER` i `approver1` s `APPROVER`. Queue manager vidí 1 fakturu a její detail/PDF; approver má 0 úkolů a manažerský seznam vrací HTTP 403.
- UI: Approval běží na `http://172.30.172.167/`; veřejná přihlašovací obrazovka a přesměrování do správného Keycloak realmu byly ověřeny v browseru. Detail má desktopový PDF/data layout a breakpoint pod 980 px; iframe a odkaz do nového okna míří na ověřený PDF proxy endpoint.
- Testy: Ruff čistý; 33 backendových testů prošlo; frontend má 2 testovací soubory / 3 testy a úspěšný TypeScript + Vite production build; `docker compose config`, `build`, `up -d` a reálný Stage B smoke test prošly.
- Nezávažné poznámky: Authlib hlásí budoucí deprecaci `authlib.jose`; host kernel neumí Compose swap limit. `npm run lint` zatím nelze použít, protože existující skript odkazuje na neinstalovaný ESLint; testy, TypeScript a build tím nejsou dotčeny.
- Nenasazeno: Ollama a LLM pipeline zůstávají záměrně mimo výchozí Compose profil a patří až do Etapy D.
- Blokátory: žádné pro Etapu B a základ Etapy C.
- POHODA: přímé připojení ani automatický import se neimplementují.
- Doporučený další krok: samostatná Etapa D — Ollama, striktní JSON extrakce a deterministická validace. Schvalování a POHODA XML následují až poté.
