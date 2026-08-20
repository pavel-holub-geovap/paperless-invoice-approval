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

