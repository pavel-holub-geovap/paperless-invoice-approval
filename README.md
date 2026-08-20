# Paperless Invoice Approval

Interní systém pro vytěžení, kontrolu, rozúčtování, paralelní schválení a manuální export přijatých faktur z externího Paperless do POHODY.

## Nejdůležitější invarianty

- Originální PDF zůstává v Paperless; aplikace ukládá jeho ID a načítá jej přes API.
- Správce fronty musí před schvalováním explicitně potvrdit kontrolu originálu.
- Schvaluje se konkrétní rozúčtovaná část za konkrétní středisko a konkrétní revizi.
- Významná změna invaliduje všechna dosavadní schválení aktuální faktury.
- Jediné `REJECT` zamítne celou fakturu; `RETURN` ji vrátí správci.
- POHODA XML generuje deterministický kód a před exportem jej validuje XSD.
- Import do POHODY je výhradně ruční. Až správce explicitně potvrdí import, vznikne stav `IMPORTED_TO_POHODA`.
- Audit je append-only. Secrets, skutečné faktury ani modely se necommitují.

## Rychlý start

1. Zkopírujte `.env.example` na `.env` a doplňte všechny hodnoty `change-me`.
2. Spusťte `docker-compose config` a zkontrolujte výslednou konfiguraci.
3. Spusťte stack: `docker-compose up -d --build`.
4. Otevřete aplikaci na `http://localhost:8080` a Keycloak na `http://localhost:8081`.

Paperless je externí a není součástí stacku. Postup jeho propojení je v [docs/PAPERLESS_INTEGRATION.md](docs/PAPERLESS_INTEGRATION.md) a přihlášení přes Keycloak v [docs/PAPERLESS_KEYCLOAK.md](docs/PAPERLESS_KEYCLOAK.md).

## Vývoj

Backend:

```text
cd backend
python -m pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload
```

Frontend:

```text
cd frontend
npm ci
npm run test
npm run build
```

Kompletní architektura, nasazení a testovací scénáře jsou v adresáři `docs/`.

