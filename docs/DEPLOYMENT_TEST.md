# Čisté nasazení kompletního testovacího prostředí

Tento návod je autoritativní postup pro nový Linux test server. Skripty jsou
záměrně nedestruktivní: neprovádějí Git pull, nemažou Docker volumes, databáze,
Paperless dokumenty, audit, exporty ani Ollama modely.

## 1. Předpoklady

Host musí mít:

- Linux, Git, Python 3, `awk`, `grep`, `sed`, `ss`, `df` a `nproc`;
- běžící Docker Engine dostupný aktuálnímu ne-root uživateli;
- Docker Compose >= 2; preferovaný je plugin `docker compose` (včetně v5),
  standalone `docker-compose` >= 2 je pouze fallback a verze 1.x není podporována;
- nejméně 4 CPU, 8 GiB RAM a 15 GiB volného místa;
- doporučeně 12 GiB RAM, swap a 30 GiB volného místa pro CPU Qwen3 8B;
- tři volné TCP porty pro Approval, Paperless a Keycloak;
- DNS jméno nebo IP dosažitelné z klienta i z Docker kontejnerů.

Testovací stack nesmí dostat produkční Paperless token, storage, databázi ani
POHODA přístup. Firewall má zveřejnit jen potřebné HTTP(S) porty důvěryhodné
testovací síti. PostgreSQL, Redis, Ollama a interní aplikační porty se na hostu
nepublikují. Pro internetově dostupný host je nutný samostatný TLS terminátor a
produkční bezpečnostní návrh; výchozí testovací HTTP konfigurace k tomu neslouží.

## 2. Checkout a bezpečná konfigurace

```bash
git clone <URL_TO_THIS_REPOSITORY> paperless-invoice-approval
cd paperless-invoice-approval
git remote -v
./scripts/generate-test-env.sh test-server.example.test
```

`generate-test-env.sh` odmítne přepsat existující `.env`, nastaví oprávnění 0600,
vytvoří každé testovací heslo nezávisle a nic citlivého nevypíše. Alternativní
soubor lze zvolit pomocí `ENV_FILE=/bezpečná/cesta/test.env`.

Před startem zkontrolujte bez zveřejnění secrets zejména:

- `APP_BASE_URL`, `PAPERLESS_PUBLIC_URL`, `KEYCLOAK_PUBLIC_URL`;
- odpovídající host bindy `APP_HOST_PORT`, `PAPERLESS_HOST_PORT`,
  `KEYCLOAK_HOST_PORT`;
- jedinečný `COMPOSE_PROJECT_NAME`, pokud na hostu běží více instancí;
- mail testovacího Paperless správce;
- volitelné `POHODA_TARGET_ICO` a `POHODA_TARGET_KEY`.

`POHODA_TARGET_ICO` je osmimístné IČO cílové účetní jednotky. Je-li prázdné,
stack funguje, ale `GENERATED_XML` export je záměrně blokovaný. `POHODA_TARGET_KEY`
ponechte prázdný, pokud nemáte skutečný GUID účetní jednotky.

## 3. Check-only a bootstrap jedním příkazem

```bash
./scripts/bootstrap-test.sh --check
./scripts/bootstrap-test.sh
```

`--check` je striktně read-only. Ověří operační systém, nástroje, Docker přístup,
Compose >= 2, povinné soubory, `.env`, izolaci interních URL/databází, sílu a
nezávislost secrets, zdroje hostu, volné publikované porty a výsledný Compose
model. Nevytvoří kontejner, síť ani volume.

Standardní bootstrap potom:

1. sestaví projektové image;
2. provede `compose up -d` bez mazání dat;
3. počká na PostgreSQL, Redis, Keycloak, Paperless a Ollamu;
4. znovu vytvoří krátkodobé provisioning kontejnery na aktuálních projektových
   sítích a ověří idempotentní Keycloak/Paperless provisioning i model;
5. počká na backend, worker, frontend a reverse proxy;
6. explicitně aplikuje všechny Alembic migrace a porovná `current` s `heads`;
7. read-only ověří DB, health endpointy, OIDC discovery, Paperless REST API,
   technické tagy, `qwen3:8b`, ISDOC XSD a POHODA XSD;
8. zkontroluje dva OIDC klienty, dvě role a čtyři unikátní testovací uživatele.

První běh stahuje image a model o velikosti několika GB. Podle připojení a CPU
může trvat desítky minut. Skript zobrazuje pouze stručný postup. Při unhealthy
stavu vypíše poslední výsledky healthchecku a krátký výřez relevantního logu;
secrets netiskne.

## 4. Stav a úplný smoke test

Read-only provozní kontrola:

```bash
./scripts/status.sh
# ekvivalentně
./scripts/bootstrap-test.sh --status
```

Report obsahuje Git commit, stav devíti dlouhodobých služeb, výsledek tří
jednorázových jobů, Alembic head, přítomnost modelu, Paperless/API a XSD kontroly.
Nemění aplikaci ani provisioning.

Skutečný syntetický tok:

```bash
./scripts/bootstrap-test.sh --full-smoke
```

Navíc přihlásí `queue-manager` i `approver1`, nahraje unikátní syntetické PDF,
počká na Paperless OCR a skutečnou Qwen3 extrakci a ověří PDF s validním vloženým
ISDOC 6.0.2, který AI správně přeskočí. Na CPU může inference trvat desítky minut.
Smoke nemaže své doklady, takže zůstávají auditovatelné v izolovaném testu.

## 5. Opakované spuštění a obnova po částečném selhání

Stejný příkaz lze spustit opakovaně:

```bash
./scripts/bootstrap-test.sh
```

Compose znovu použije projektové volumes. Databázové migrace, seed, Keycloak
realm/clients/role/users, Paperless technické tagy a servisní API token jsou
idempotentní. Existující Ollama model se znovu nestahuje. Pokud běh skončí kvůli
síti, nedostatku místa, nehealthy službě nebo chybnému `.env`, opravte příčinu a
spusťte tentýž příkaz; předchozí data zůstanou zachována.

Diagnostika bez velkých výpisů:

```bash
./scripts/status.sh
docker compose logs --tail=100 <service>
docker compose ps -a
```

Bez výslovného souhlasu nepoužívejte `down -v`, Docker prune, mazání volumes,
databází, Paperless storage ani dokumentů. Běžný restart konkrétní služby nebo
opakovaný `up -d` je bezpečný.

## 6. Více izolovaných testovacích instancí

Každá instance musí mít vlastní `COMPOSE_PROJECT_NAME`, trojici host portů a `.env`.
Compose sítě i volumes jsou projektově pojmenované; instance proto nesdílejí
PostgreSQL, Redis, Paperless data, API token ani exporty. Ollama cache lze při
řízeném integračním testu připojit jako explicitní externí volume, jinak je rovněž
izolovaná.

```bash
ENV_FILE=/secure/test-b.env ./scripts/bootstrap-test.sh --check
ENV_FILE=/secure/test-b.env ./scripts/bootstrap-test.sh
```

Příklad druhé instance na sdíleném hostu:

```dotenv
COMPOSE_PROJECT_NAME=paperless-invoice-test2
APP_HOST_PORT=18080
PAPERLESS_HOST_PORT=18000
KEYCLOAK_HOST_PORT=18081
APP_BASE_URL=http://test-server.example:18080
PAPERLESS_PUBLIC_URL=http://test-server.example:18000
KEYCLOAK_PUBLIC_URL=http://test-server.example:18081
```

Host port je lokální published port Dockeru. Public URL je adresa používaná
prohlížečem, OIDC issuerem a callbacky. Při přímém přístupu obvykle obsahuje
stejný port; za externím reverse proxy nebo NAT se může lišit a validace pouze
upozorní. Provisioning používá interně `http://keycloak:8080`; Paperless,
backend i worker používají Compose DNS a nejsou závislé na host bindu ani Nginx.

Cílený read-only test výsledného Compose modelu s nestandardními porty:

```bash
./scripts/test-deployment-config.sh
```

Test spustí skutečný `docker compose config --format json` a ověří project name,
trojici bindů 28080/28000/28081 a skutečnost, že jiné služby porty nepublikují.

Pouze pro vědomé sdílení již staženého modelu nastavte v chráněném env souboru
`OLLAMA_CACHE_VOLUME` na přesný název existujícího modelového volume a spusťte:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.ollama-cache.yml \
  ENV_FILE=/secure/test-b.env ./scripts/bootstrap-test.sh
```

Override nesdílí žádnou databázi, Paperless storage, token ani export. Ollama
cache není autoritou aplikačních dat. Bez tohoto explicitního override má každá
instance vlastní modelové volume.

Bootstrap nesmí být spuštěn s `APP_ENV=production`.

## 7. Zálohy, migrace a obnova

Před upgrade s významnou migrací vytvořte provozně schválenou zálohu Approval,
Keycloak a Paperless databází a Paperless media/export volumes. Zálohujte také
Approval export volume a chráněný `.env`; API token lze znovu idempotentně
provisionovat, ale jeho volume běžně zachovejte. Ollama model je cache a lze jej
znovu stáhnout, není autoritou účetních dat.

Obecné bezpečné pořadí upgradu:

```bash
git fetch origin
git pull --ff-only
./scripts/bootstrap-test.sh --check
./scripts/bootstrap-test.sh
./scripts/status.sh
```

Bootstrap provede dopředné Alembic migrace. Automatický downgrade ani obnova
databáze se neprovádí. Při neúspěšné migraci zachovejte kontejnery a volumes,
uložte log, opravte příčinu v Git historii a spusťte bootstrap znovu. Obnova ze
zálohy je samostatná potenciálně destruktivní operace a vyžaduje výslovné
rozhodnutí správce.

## 8. Hranice vůči produkci a POHODĚ

- Žádná služba se nepřipojuje přímo do databáze Paperless ani POHODY.
- Approval a worker používají Paperless pouze přes REST API a chráněný servisní
  token uložený v Docker volume.
- Ollama pouze navrhuje striktní extraction JSON; nevytváří XML, SQL, workflow,
  allocations ani assignments.
- POHODA XML vzniká deterministicky z immutable schválené revize, projde XSD i
  samostatnou kontrolou cílové jednotky a pouze se stáhne pro ruční import.
- Testovací Keycloak uživatelé, secrets a dokumenty se nesmějí kopírovat do
  produkce. Produkční nasazení vyžaduje samostatný hardening, TLS, rotaci secrets,
  monitoring, zálohy a recovery drill.
