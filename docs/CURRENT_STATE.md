# Aktuální stav

- Datum: 2026-08-23
- Branch: `main`
- Git remote: není nakonfigurovaný; push ani serverový checkout nejsou možné bez přesné GitHub SSH URL.
- Implementováno: kompletní Compose návrh s izolovaným Paperless-ngx, Redis, třemi PostgreSQL databázemi/uživateli, dvěma Keycloak OIDC clients, provisioningem rolí/skupin/tagů a runtime Paperless API tokenu, persistence, healthchecky a tříportovou Nginx vstupní vrstvou.
- Testovací dokument: image-only syntetická česko-anglická faktura bez reálných údajů, určená pro upload/OCR smoke test.
- Otestováno: Ruff; 23 backendových testů; frontendový Vitest, TypeScript a produkční Vite build; statická kontrola 11 služeb/9 volumes; image-only PDF kontrola; skutečný `docker compose -f - config --quiet` na Linux VM skončil kódem 0.
- Nenasazeno: žádný nový kontejner nebyl na VM spuštěn; PostgreSQL, Redis, Keycloak, Paperless ani další služby nelze označit za funkční.
- VM/RAM: SSH je znovu dostupné; aktuálně 11 GiB RAM, přibližně 11 GiB available a 4 GiB nepoužitého swapu. Na VM se nic nenasadilo.
- Blokátory: chybí přesná GitHub SSH URL/remote a serverová `.env` se skutečnými secrets.
- POHODA: přímé připojení ani automatický import se neimplementují.
- Doporučený další krok: získat GitHub SSH URL, obnovit SSH dostupnost VM, připravit serverovou `.env`, znovu ověřit `free -h` a teprve poté spustit Etapu A z `docs/DEPLOYMENT.md`.
