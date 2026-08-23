# Integrační a testovací prostředí

Stav inventury: 2026-08-23. Inventura byla provedena pouze čtecími příkazy přes neinteraktivní SSH. Na VM nebyl vytvořen adresář, změněna konfigurace ani spuštěn deployment.

## Přístup a účel

- SSH alias: `ubuntudocker`
- Připojení: `ssh ubuntudocker`
- Linux uživatel: `codex`
- Hostname: `ubuntudocker`
- SSH v režimu `BatchMode=yes`: funkční bez interaktivního dotazu
- Účel: vyhrazená Linux VM pro integrační testy a testovací Docker deployment projektu
- Zamýšlený projektový adresář: `/opt/paperless-invoice-approval`

SSH klíče, hesla, tokeny a další secrets jsou spravovány mimo repozitář a v tomto dokumentu nejsou uvedeny.

## Systém

| Položka | Zjištěná hodnota |
| --- | --- |
| OS | Ubuntu 20.04.6 LTS (Focal Fossa) |
| Kernel | Linux 5.4.0-216-generic, x86_64 |
| Virtualizace | Microsoft hypervisor, plná virtualizace |
| CPU | 8 vCPU, 1 socket / 4 cores / 2 threads |
| RAM | 7,8 GiB celkem; při inventuře 1,1 GiB dostupné |
| Swap | 4,0 GiB; při inventuře 0 B použito |
| Root disk | 62 GiB celkem, 12 GiB použito, 47 GiB volno (20 %) |
| Git | 2.25.1 |
| Docker Engine | 28.1.1 |
| Docker Compose | plugin v2.35.1 |

Příkaz `docker-compose` je na VM pouze krátký kompatibilní wrapper nad `docker compose`; pro nové příkazy lze používat obě varianty.

## Docker stav a oprávnění

- Uživatel `codex` je člen skupiny `docker`.
- `docker info`, `docker ps`, výpis volumes a networks fungují bez `sudo`.
- Při inventuře neběžel žádný kontejner.
- Na hostiteli existovalo šest lokálních Docker volumes a šest Docker networks z jiných projektů nebo výchozí instalace. Nejsou součástí tohoto projektu a nesmí se mazat ani prořezávat.
- Projektový adresář `/opt/paperless-invoice-approval` neexistoval.
- `/opt` vlastní `root:root`, má oprávnění `755` a uživatel `codex` do něj nemůže zapisovat.

Vytvoření projektového adresáře proto vyžaduje jednorázovou autorizovanou administrátorskou operaci, například vytvoření adresáře a předání vlastnictví `codex:codex`. Dokud ji uživatel výslovně neschválí nebo neurčí jiný zapisovatelný adresář, deployment se nesmí zahájit.

## Síťová dostupnost

| Cíl | Výsledek |
| --- | --- |
| DNS `github.com` | OK |
| HTTPS GitHub | HTTP 200 |
| DNS `registry-1.docker.io` | OK |
| Docker Registry HTTPS | HTTP 401; očekávaná odpověď neautentizovaného registry endpointu potvrzuje dostupnost |
| DNS `registry.ollama.ai` | OK |
| Ollama Registry HTTPS | HTTP 404; endpoint je síťově a TLS dostupný |
| Externí Paperless | Netestováno; projekt obsahuje pouze placeholder `paperless.example.invalid` a lokální `.env` neexistuje |

Lokální Git repozitář v době inventury neměl nakonfigurovaný remote. Před workflow založeným na `git pull --ff-only` je proto potřeba znát a nastavit správný GitHub remote; remote se nesmí odhadovat.

## Plánované služby a porty

| Služba | Port / dostupnost |
| --- | --- |
| PostgreSQL | interní `5432` |
| Keycloak | host `8081` → kontejner `8080` |
| Ollama | interní `11434` |
| Backend | interní `8000` |
| Worker | bez HTTP portu |
| Frontend | interní `80` |
| Reverse proxy | host `8080` → kontejner `80` |
| Keycloak provision | jednorázová úloha bez portu |

Paperless je externí a není součástí tohoto stacku. POHODA není k VM připojena a XML se importuje ručně.

## Rizika před prvním deploymentem

1. Chybí zapisovatelný projektový adresář pod `/opt`.
2. Chybí Git remote a tím i zdroj pro serverový checkout.
3. Chybí skutečná `.env` konfigurace včetně bezpečně uložených secrets a reálné Paperless URL.
4. Přestože neběžely žádné kontejnery a seznam procesů neukázal velkého uživatelského spotřebitele, VM hlásila pouze 1,1 GiB dostupné RAM. Před stažením a spuštěním LLM je potřeba vysvětlit obsazení paměti a znovu změřit `free -h` a `docker stats`, jinak hrozí OOM.

## Povolený další krok

Po vyřešení adresáře, Git remote a secrets lze provést samostatně schválený první deployment. Bez výslovného souhlasu se nesmí měnit systém, firewall nebo SSH, mazat volumes, resetovat databáze, používat prune příkazy ani zapisovat do externího Paperless či POHODY.
