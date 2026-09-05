# Paperless Invoice Approval

Interní aplikace pro příjem, klasifikaci, vytěžení a věcné schvalování přijatých
dokladů. Originál a OCR spravuje Paperless-ngx, přihlášení Keycloak, strukturované
vytěžení lokální Ollama/Qwen3 a účetní předání vzniká jako schválené PDF s ISDOC
nebo deterministické POHODA XML. Import do POHODY zůstává ruční.

## Co stack obsahuje

- Paperless-ngx pro originály, přílohy, OCR a technické tagy;
- Approval FastAPI backend, worker a responzivní React UI;
- Keycloak/OIDC s rolemi `QUEUE_MANAGER` a `APPROVER`;
- oddělené databáze Approval, Keycloak a Paperless v PostgreSQL;
- Redis, CPU-only Ollamu s modelem `qwen3:8b` a Nginx reverse proxy;
- lokální ISDOC 6.0.2 a POHODA XSD bundle bez přístupu do účetního systému.

Approval nikdy nečte databázi Paperless přímo. Originální PDF neduplikuje do své
databáze a Paperless token neposílá do prohlížeče. Audit i AI běhy jsou append-only.
Schvalovatel může stejnou zabezpečenou cestou nahrát vlastní PDF, připravit jeho
sekce a schválit povolené části; finální postup vždy vyžaduje kontrolu konkrétní
revize správcem fronty.

## První testovací nasazení na Linuxu

Požadavky: moderní Linux, Git, Docker Engine, Docker Compose >= 2, Python 3,
nejméně 4 CPU, 8 GiB RAM a 15 GiB volného místa. Pro Qwen3 doporučujeme 12 GiB
RAM a 30 GiB volného místa. Uživatel musí mít běžný přístup k Docker daemonu.
Preferovaný příkaz je plugin `docker compose`; podporované jsou i novější
kompatibilní major verze včetně v5. Standalone `docker-compose` >= 2 je pouze
fallback a vedlejší instalace `docker-compose` 1.x se ignoruje.

```bash
git clone <URL_TO_THIS_REPOSITORY> paperless-invoice-approval
cd paperless-invoice-approval
./scripts/generate-test-env.sh test-server.example.test
./scripts/bootstrap-test.sh --check
./scripts/bootstrap-test.sh
```

Generátor vytvoří necommitovaný `.env` s nezávislými náhodnými testovacími
secrets a nic citlivého nevypíše. Před startem lze upravit veřejné hostname,
publikované porty a volitelné POHODA hodnoty. První start stáhne image a přibližně
několik GB modelových dat, proto může trvat výrazně déle než další spuštění.

Na sdíleném Docker hostu nastavte unikátní project name a volné host porty;
interní porty a service DNS se nemění:

```dotenv
COMPOSE_PROJECT_NAME=paperless-invoice-test2
APP_HOST_PORT=18080
PAPERLESS_HOST_PORT=18000
KEYCLOAK_HOST_PORT=18081
APP_BASE_URL=http://test-server.example:18080
PAPERLESS_PUBLIC_URL=http://test-server.example:18000
KEYCLOAK_PUBLIC_URL=http://test-server.example:18081
```

`*_HOST_PORT` řídí lokální Docker bind. Veřejná URL řídí odkazy v prohlížeči,
OIDC issuer a callbacky; při externím proxy/NAT se proto smí lišit a preflight
vypíše pouze upozornění. `--check` ověří porty před vytvořením Docker objektů.

Po dokončení otevřete URL uvedené bootstrapem:

- Approval: `APP_BASE_URL`;
- Paperless: `PAPERLESS_PUBLIC_URL`;
- Keycloak: `KEYCLOAK_PUBLIC_URL`.

Testovací identity jsou `queue-manager`, `approver1`, `approver2` a `approver3`.
Jejich hesla jsou pouze v chráněném `.env`. Provozní stav bez změny dat ověří:

```bash
./scripts/status.sh
```

Bootstrap je idempotentní. Lze jej spustit znovu po úspěchu i po částečném
selhání; nemaže volumes, databáze, dokumenty, audit, exporty ani modely.
Podrobný čistý deployment, obnova po chybě, zálohování a síťové požadavky jsou v
[docs/DEPLOYMENT_TEST.md](docs/DEPLOYMENT_TEST.md).

## Ověření celého toku

Standardní bootstrap ověřuje služby, provisionované role/uživatele/klienty,
Paperless tagy, REST integraci, migrace, model a oba XSD bundle. Volitelná úplná
zkouška navíc nahraje pouze syntetické doklady a provede skutečné OCR a Qwen3
inferenci:

```bash
./scripts/bootstrap-test.sh --full-smoke
```

Tento test může na CPU běžet desítky minut. Vytvořené syntetické doklady zůstávají
v izolovaném testovacím prostředí jako dohledatelná historie.

## Vývoj a regrese

```bash
cd backend
python -m pip install -e ".[dev]"
pytest

cd ../frontend
npm ci
npm run test
npm run build
npm run lint
```

Další statická kontrola Compose je `python scripts/validate_compose.py`. Integrační
a smoke scénáře jsou popsány v [docs/TESTING.md](docs/TESTING.md).

## Doménové zásady

- Workflow stav mění pouze centralizovaná služba a významná změna vytváří novou
  revizi a invaliduje stará schválení bez mazání historie.
- Schvalují se allocations konkrétní revize. `RETURN` a `REJECT` vyžadují komentář.
- Validní vložený ISDOC je primární zdroj a AI přeskočí. Jinak Qwen převádí OCR
  pouze do striktního schématu; neurčuje workflow, střediska ani schvalovatele.
- XML generuje deterministický backend ze schváleného immutable snapshotu. XSD
  validita a cílová účetní jednotka jsou dvě samostatné kontroly.
- `dataPack/@ico` smí pocházet jen z `POHODA_TARGET_ICO`. Bez cílového IČO je
  generovaný XML export blokovaný; bez explicitního GUID se `key` nevytváří.
- `EXPORT_CREATED` neznamená import. `IMPORTED_TO_POHODA` vyžaduje explicitní
  potvrzení správce po ručním importu.

## Dokumentace

- [Architektura](docs/ARCHITECTURE.md)
- [Testovací deployment](docs/DEPLOYMENT_TEST.md)
- [Testování](docs/TESTING.md)
- [Schvalovací workflow](docs/APPROVAL_WORKFLOW.md)
- [Paperless integrace](docs/PAPERLESS_INTEGRATION.md)
- [Keycloak/OIDC](docs/PAPERLESS_KEYCLOAK.md)
- [AI extrakce](docs/AI_EXTRACTION.md)
- [ISDOC](docs/ISDOC.md)
- [POHODA export](docs/POHODA_EXPORT.md)
- [Aktuální ověřený stav](docs/CURRENT_STATE.md)

Produkční prostředí musí mít vlastní secrets, hostname/TLS, zálohy a provozní
politiku. Testovací `.env`, identity, data ani Paperless token se do produkce
nepřenášejí.
