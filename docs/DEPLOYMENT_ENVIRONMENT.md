# Integrační a testovací prostředí

## Přístup a účel

- SSH alias: `ubuntudocker`
- Linux uživatel: `codex`
- Hostname: `ubuntudocker`
- Projektový adresář: `/home/codex/paperless-invoice-approval`
- Účel: kompletní izolovaný testovací stack včetně Paperless-ngx

SSH klíče, hesla, tokeny a další secrets jsou spravovány mimo repozitář.

## Poslední úspěšná inventura (2026-08-23)

| Položka | Hodnota |
| --- | --- |
| OS | Ubuntu 20.04.6 LTS, kernel 5.4.0-216-generic, x86_64 |
| CPU | 8 vCPU |
| RAM | původně 7,8 GiB; po obnovení dynamické paměti 11 GiB celkem a přibližně 11 GiB dostupných |
| Swap | 4,0 GiB |
| Root disk | 62 GiB celkem, 47 GiB volno |
| Git | 2.25.1 |
| Docker Engine | 28.1.1 |
| Docker Compose | v2.35.1 |
| Docker bez sudo | Ano, `codex` je člen skupiny `docker` |

Původní adresář `/opt/paperless-invoice-approval` se již nepoužívá. Nová cesta v home adresáři nevyžaduje administrátorský zásah.

## Stav dostupnosti 2026-08-23

Po dvou dočasných SSH timeoutech se přístup obnovil. Nové `free -h` ukázalo 11 GiB RAM, 294 MiB použitých, přibližně 11 GiB dostupných a 4 GiB nepoužitého swapu. Docker Compose v2.35.1 je dostupný.

Cílový adresář `/home/codex/paperless-invoice-approval` zatím neexistuje, ale `/home/codex` je pro uživatele `codex` zapisovatelný, takže jeho budoucí vytvoření nevyžaduje `sudo`. Na VM nebyla provedena žádná změna ani deployment. Existující volumes a networks nebyly změněny a nesmí být mazány.

Finální `docker-compose.yml` byl odeslán pouze přes standardní vstup do `docker compose -f - config --quiet` s placeholdery z `.env.example`; validace skončila kódem 0 a na VM nevytvořila soubor, image, kontejner ani volume.

## Cílové služby, image a porty

| Služba | Image/verze | Veřejný port |
| --- | --- | --- |
| PostgreSQL | `postgres:17-alpine` | žádný |
| Redis | `redis:8.10.0-alpine` | žádný |
| Keycloak | `quay.io/keycloak/keycloak:26.6.4` | přes Nginx `8081` |
| Paperless | `ghcr.io/paperless-ngx/paperless-ngx:3.0.5` | přes Nginx `8000` |
| Ollama | `ollama/ollama:0.32.7` | žádný |
| Approval backend/worker | lokální build | přes Nginx/backend interně `8000` |
| Approval frontend | lokální build | přes Nginx `80` |
| Reverse proxy | `nginx:1.27-alpine` | `80`, `8000`, `8081` |

Veřejné testovací URL jsou Approval `http://172.30.172.167/`, Paperless `http://172.30.172.167:8000/` a Keycloak `http://172.30.172.167:8081/`.

Porty `80`, `8000` a `8081` byly při kontrole volné (žádný naslouchající socket).

## Blokátory prvního deploymentu

1. Lokální repozitář nemá nakonfigurovaný Git remote; je potřeba přesná GitHub SSH URL.
2. Serverová `.env` a skutečné secrets nejsou připravené.
3. Cílový checkout zatím neexistuje; vytvoří se až klonováním známého správného GitHub remote.

Dokud nejsou Git remote a serverové secrets vyřešeny, nesmí se zahájit Etapa A.
