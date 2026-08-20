# Testování

Backend používá `pytest` a izolovanou SQLite databázi pro rychlé doménové testy; PostgreSQL integrační sada běží v Compose. Testy pokrývají validace, stavový automat, revize a invalidaci, approvals, rozúčtování, audit, XML a export.

Frontend používá Vitest + Testing Library; Playwright E2E scénáře jsou navržené pro běžící Compose stack a seed realm.

Před předáním spusťte:

```text
cd backend
pytest

cd ../frontend
npm ci
npm run test
npm run build

cd ..
docker-compose config
docker-compose build
```

Integrační testy, které mění externí Paperless tagy, jsou opt-in a vyžadují explicitní testovací tenant a potvrzení.

