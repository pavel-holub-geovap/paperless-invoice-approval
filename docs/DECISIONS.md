# Architektonická rozhodnutí

## ADR-001: PostgreSQL databázová fronta

Pro sekvenční AI inference a nízký objem testovacího systému používáme `processing_jobs` s row lockingem namísto Redis/Celery. Snižuje to paměť i počet komponent a zachovává transakční idempotenci.

## ADR-002: OIDC Backend-for-Frontend session

Frontend neukládá access token do `localStorage`. Backend dokončí Authorization Code flow, vytvoří serverovou session a browser dostane pouze opaque HttpOnly cookie.

## ADR-003: Finanční částky jako Decimal

Částky jsou PostgreSQL `NUMERIC` a Python `Decimal`; float je zakázán. Procentní rozdělení používá largest-remainder korekci na poslední haléře s deterministickým tie-breakem podle pořadí/střediska.

## ADR-004: Snapshot revize v JSON + normalizované allocations

Účetní hlavička revize je neměnný JSON snapshot, zatímco allocations a assignments jsou normalizované kvůli integritě a dotazům. Audit uchovává before/after.

## ADR-005: UI technologie

Používáme doporučený React + TypeScript + Vite a produkční statický build obsluhovaný Nginx.

## ADR-006: ARES je volitelné obohacení

ARES není v základní vertikále povinný. Rozhraní externích validátorů je připravené, ale jeho výpadek nikdy nesmí být blokující.

## ADR-007: Kompletní izolovaný testovací Paperless

Testovací VM obsahuje vlastní Paperless-ngx 3.0.5, databázi `paperless`, Redis a samostatná persistentní volumes. Tato instalace nesmí používat produkční storage, databázi ani token. Produkční topologie může později používat externí Paperless, ale REST API a `paperless_document_id` zůstávají integrační hranicí.

## ADR-008: Jedna reverse proxy, tři porty

Kvůli spolehlivým OIDC issuer a callback URL přes IP adresu nepoužíváme URL subpath. Jediný Nginx publikuje Approval na `:80`, Paperless na `:8000` a Keycloak na `:8081`; cílové kontejnery své porty hostiteli nepublikují. Oddělené porty jsou pro testovací IP technicky čistší než přepisování cest a statických URL Paperless/Keycloak.

## ADR-009: Oddělené OIDC clients a runtime Paperless token

Keycloak provisioning vytváří clients `approval-app` a `paperless` s různými secrets, realm roles i skupinami `QUEUE_MANAGER` a `APPROVER`. Paperless provisioning vytváří testovací skupiny/tagy a uloží API token do samostatného Docker volume; token není v browseru ani Gitu. Testovací service account má kvůli objektovým oprávněním široký přístup pouze v izolovaném tenantovi; produkce musí použít nejmenší nutná oprávnění.
