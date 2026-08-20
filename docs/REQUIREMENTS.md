# Funkční a nefunkční požadavky

Zdrojovým implementačním kontraktem je `Zadání sw-paperless-invoice-full.docx`. Tento dokument převádí jeho požadavky do kontrolovatelných oblastí.

## Workflow faktury

1. Synchronizace objeví právě jednou Paperless dokument označený konfigurovatelným vstupním tagem.
2. Worker načte OCR text a požádá lokální Ollama model o JSON podle striktního schématu, s teplotou 0 a `null` pro neznámé údaje.
3. U významných polí uloží hodnotu i zdrojový text. AI nikdy nevytváří účetní částky ani POHODA XML.
4. Deterministická validace rozliší `OK`, `WARNING` a `BLOCKING_ERROR`.
5. Správce porovná originální PDF, OCR, extrakci a validace, může data auditovaně opravit a explicitně potvrdí kontrolu originálu.
6. Fakturu rozúčtuje částkou nebo procentem na aktivní střediska; interně se ukládá konkrétní částka a součet musí sedět v nakonfigurované toleranci.
7. Každá allocation má jednoho až N povinných schvalovatelů. Správce je může měnit i během procesu.
8. Schvalování je paralelní. `APPROVE` schválí assignment; `RETURN` s komentářem vrátí fakturu správci; `REJECT` s komentářem zamítne celou fakturu.
9. Významná změna vytvoří novou revizi a invaliduje všechna stará schválení. Historie se nemaže.
10. Faktura je finálně schválená až po kontrole originálu, bez blokujících chyb, s přesným rozúčtováním a všemi povinnými approvals.
11. Deterministický generátor vytvoří XML přijaté faktury se středisky a ověří je proti deklarované XSD sadě.
12. Správce stáhne XML, originální PDF nebo ZIP; může vytvořit dávku více faktur. Export neznamená import.
13. Po skutečném ručním importu správce jednotlivě nebo pro dávku explicitně nastaví `IMPORTED_TO_POHODA`.
14. Hrubý stav se synchronizuje Paperless tagy; detailní stav, approvals a audit jsou autoritativní v PostgreSQL.

## Stavy

`NEW`, `AI_PROCESSING`, `VALIDATION`, `QUEUE_REVIEW`, `NEEDS_REVIEW`, `READY_FOR_APPROVAL`, `AWAITING_APPROVAL`, `RETURNED`, `REJECTED`, `APPROVED`, `XML_READY`, `READY_FOR_EXPORT`, `EXPORT_CREATED`, `IMPORTED_TO_POHODA`.

Přechody jsou centralizované. API nesmí stav libovolně přepisovat.

## Data a integrita

Minimální entity: identita uživatele, faktura, revize, vytěžené pole, výsledek validace, středisko, allocation, assignment, rozhodnutí, auditní událost, job, exportní dávka a její položka. Unikátní vazba chrání před duplicitou Paperless ID. Backend a databázové constraints brání schválení staré revize, exportu neschválené faktury a rozhodnutí bez assignmentu.

## Validace

Povinné kontroly: formát a český kontrolní součet IČO, formát DIČ, data, číslo faktury, variabilní symbol, měna, DPH a daňové součty, celková částka, povinná pole, duplicita a matematická konzistence allocations. Externí ARES je volitelný, pouze varuje a jeho nedostupnost neblokuje systém.

## Identita a role

Keycloak je společný OIDC provider pro aplikaci a existující Paperless. Aplikace používá bezpečný serverový session model s HttpOnly cookie; Paperless token není v browseru. Počáteční role jsou `QUEUE_MANAGER` a `APPROVER`, model umožňuje doplnit `SYSTEM_ADMIN`, `ACCOUNTANT`, `AUDITOR` a `VIEWER`. Testovací hesla přicházejí pouze z prostředí/secrets.

## UI

Responzivní React/TypeScript/Vite UI funguje na desktopu, tabletu i telefonu. Správce má filtrovatelný dashboard, detail s PDF vedle dat, validací, allocations a approvers, audit a historii exportů. Schvalovatel vidí čekající, dokončené, vrácené a historii; u úkolu jasně vidí celkovou i vlastní částku, středisko, PDF a warnings.

## Integrace a provoz

- Externí Paperless REST API: timeout, retry, idempotence a konfigurovatelné tagy.
- Ollama a model jsou konfigurovatelné; výchozí model `qwen3:4b`, CPU, sekvenční inference a konzervativní kontext.
- Background joby jsou v PostgreSQL, obnovitelné a idempotentní; Redis není nutný.
- Jeden `docker-compose` stack obsahuje PostgreSQL, Keycloak, Ollama, backend, worker, statický frontend a reverse proxy. Paperless v něm není.
- Persistent volumes, healthchecks, restart policies a rozumné limity musí zachovat workflow, audit i exporty po restartu.
- Logy nesou invoice/paperless/job/transition/actor kontext, ale nikdy secrets, Authorization hlavičky ani celý dokument.

## Bezpečnost

Repozitář nesmí obsahovat skutečné faktury, secrets, produkční certifikáty, runtime databáze ani modely. `.env.example` obsahuje jen placeholdery. Všechny externí integrace mají čitelné chyby, timeouty, retry a idempotenci.

## Povinné testy

- Jedno i více středisek, paralelní schvalování, více schvalovatelů a jeden schvalovatel na více allocations.
- Povinné komentáře pro `RETURN`/`REJECT`, návrat a znovupředání, globální účinek `REJECT`.
- Invalidace po změně významných polí, střediska, částky allocation a seznamu approvers.
- Přesný, neúplný a přečerpaný součet, procenta a hraniční zaokrouhlení.
- Zákaz exportu před schválením, individuální i batch export, PDF z Paperless, XSD-validní XML a oddělení export/import.
- Append-only audit s before/after a vazbou approvals na revizi.
- Šest E2E scénářů popsaných ve zdrojovém zadání.

## Mimo rozsah

Bez dalšího zadání se neimplementuje přímý import či zápis do POHODY, automatické zaúčtování, e-mailové/Teams notifikace, sekvenční approval chains, zastupování, částkové limity ani účetní předkontace.

