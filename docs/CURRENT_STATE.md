# Aktuální stav

- Datum: 2026-08-20
- Branch: `main` (lokální repozitář bez nakonfigurovaného remote)
- Commit: úvodní předávací commit obsahuje kompletní první implementaci podle zadání; přesný hash vrátí `git rev-parse HEAD`.
- Implementováno: FastAPI backend, PostgreSQL datový model a migrace, stavový automat s revizemi a paralelním schvalováním, append-only audit, Paperless REST klient, Keycloak OIDC BFF, Ollama extrakce, fronta úloh, POHODA XML/XSD export, React/Vite frontend a Docker Compose infrastruktura.
- Otestováno: Ruff; 20 backendových testů; Alembic migrace na izolované databázi; kompilace OpenAPI; oficiální POHODA XSD graf; frontendový Vitest, TypeScript a produkční Vite build; syntaktické načtení Compose YAML.
- Neověřeno v tomto prostředí: `docker-compose config/build/up`, kompletní browser E2E a komunikace s reálným Paperless, Keycloak, Ollama a POHODOU. Docker ani LibreOffice zde nejsou dostupné a vestavěný prohlížeč se nepodařilo spustit kvůli lokálnímu oprávnění `EPERM`.
- Externí mutace: nebyly provedeny žádné zápisy do skutečného Paperless ani POHODY.
- Známá omezení: POHODA import zůstává záměrně ruční; aplikace pouze vytvoří a XSD-validuje export a následně umožní explicitní potvrzení importu. Vizuální kontrola zdrojového DOCX nebyla kvůli chybějícímu LibreOffice provedena, celý text dokumentu však byl strojově načten.
- Potřebná konfigurace: zkopírovat `.env.example` na `.env`, nahradit všechny hodnoty `change-me`, doplnit URL/token testovacího Paperless a stáhnout požadovaný Ollama model.
- Doporučený další krok: na hostiteli s Dockerem spustit Compose stack, provést seed testovacích dat a projít scénáře z `docs/TESTING.md` proti odděleným testovacím službám.
