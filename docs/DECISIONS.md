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

## ADR-010: Bezpečná invalidace celé faktury

Po zahájení schvalování změna významného fakturačního pole, allocation, střediska nebo seznamu approverů vytváří novou revizi a invaliduje všechna starší platná rozhodnutí i assignmenty faktury. Nezachováváme ani zdánlivě nedotčená schválení; jednoznačnost a auditovatelnost mají přednost před optimalizací.

## ADR-011: Paralelní approvals s řádkovým zámkem

Schvalovatelé nemají pořadí. Každé rozhodnutí zamkne fakturu a assignment v jednotném pořadí a databáze má částečný unikátní index na platné rozhodnutí assignmentu. Opakovaný shodný `APPROVE` vrací původní rozhodnutí, konfliktní nebo zastaralý požadavek je odmítnut.

## ADR-012: Verzovaný offline POHODA XSD bundle

Validace používá úplný oficiální POHODA XML 2.x bundle s datem 2025-10-16, uložený v repozitáři. Schémata se nestahují při exportu. Aktualizace bundle je auditovatelná změna zdroje, checksumů, aktivní verze a regresních testů.

## ADR-013: Immutable exportní artifact

Každé generování ukládá neměnný snapshot aktuální revize, allocations a approvals spolu s verzemi a SHA-256 XML/PDF. Re-export vytváří nový řádek a odkazuje na předchozí artifact. Stažení nic nemění; `IMPORTED_TO_POHODA` vzniká pouze explicitním potvrzením správce po ručním importu.

## ADR-014: Účetní DPH split se neodhaduje

Jedna sazba dovoluje deterministické largest-remainder rozdělení základu mezi střediska. U více sazeb a více středisek model vyžaduje explicitní `Allocation.vat_breakdown`; bez něj export končí blokující chybou. Správnost a reprodukovatelnost mají přednost před automatizací.

## ADR-015: Tři nezávislé osy stavu faktury

Workflow popisuje business proces, dispozice vědomé vyřazení a source status fyzickou dostupnost Paperless originálu. Nezavádíme pseudo-workflow stavy pro duplicitu nebo 404, protože by přepisovaly účetní historii. Ignorování i source přechody jsou append-only auditované a slouží jako guard podmínky nad existujícím workflow.

## Approval DB rozhoduje o přístupu, Paperless pouze hledá

Pro „Moji historii“ nepoužíváme Paperless permissions ani browser-side filtrování. Oprávněná množina vzniká v Approval DB z libovolného historického assignmentu uživatele. Paperless REST fulltext vrací kandidátní document ID a backend provede bezpečný průnik ještě před stránkováním a serializací. Tím nemohou uniknout cizí metadata, snippet ani počet výsledků. Vlastní druhý OCR index nezavádíme; Paperless zůstává fulltext backendem.

Historický detail je samostatný read-only kontrakt. Nevrací technický audit ani editovací operace a uchovává snapshot dat každé rozhodované revize. Obecný manager detail zůstává oddělený.

## ADR-016: Jen potvrzená 404 znamená MISSING

Reconciliation čte každý známý dokument přes REST. Pouze HTTP 404 je důkaz neexistence. Auth chyba, timeout, síťová chyba a 5xx zůstávají retryovatelnou integrační chybou, aby výpadek Paperless nemohl hromadně označit faktury jako smazané.

## ADR-017: Deterministická normalizace účtu a review DPH rozdíly

LLM poskytuje hodnotu a evidence, nikoli strukturu českého clearingového účtu. Parser bez odhadu rozloží `[prefix-]number/code` a POHODA v2 mapuje účet/kód odděleně. Tři agregované DPH reconciliation rozdíly jsou WARNING, protože mohou představovat legitimní zaokrouhlení; řádkové a strukturální chyby zůstávají blocking.

## Deterministická autorita pro zaokrouhlení

LLM smí navrhnout `ROUNDING`, ale normalizační hranice jej přijme pouze s explicitním samostatným štítkem zaokrouhlení v `source_text`. Souhrnný VAT/total řádek se zachová jako běžný VAT řádek a falešná klasifikace se uloží jen do append-only raw/normalizační diagnostiky. Samotná matematická odchylka je reconciliation warning a bez dokumentového důkazu se automaticky nenazývá zaokrouhlením.

## ADR-018: Cílová identita POHODA, 8B model a polling

- `dataPack/@ico` je povinná cílová účetní jednotka ze serverové konfigurace; `key` je pouze explicitní volba.
- XSD stav a sémantika cílové účetní jednotky jsou nezávislé výsledky. `XSD_VALID` neznamená `TARGET_UNIT_VALID`; download a ZIP znovu ověřují hash i cílové atributy immutable bytes.
- Výchozí extrakční model je Qwen3 8B bez automatického fallbacku. Historické běhy zůstávají nedotčené.
- Pro živou aktualizaci byl zvolen polling; websocket infrastruktura by nepřinesla úměrný přínos.
- Optimistická konkurence používá číslo doménové revize a HTTP 409. Polling nikdy nepřepisuje dirty formulář.

## ADR-019: Serverový snapshot a formulářový draft jsou oddělené zdroje stavu

Detail API je autorita pro current invoice revision, evidence, AI kandidáta, workflow a validace. Editovatelný formulář drží pouze lokální draft odvozený z current dat. Hydratace se řídí fingerprintem všech editovatelných hodnot a allocations, nikoli jen číslem revize, protože první automatická AI aplikace legitimně naplní počáteční revizi beze změny jejího čísla. Nový serverový snapshot automaticky naplní pouze čistý draft; dirty draft zůstane zachovaný a UI nabídne explicitní načtení serverové verze.

Raw `parsed_result`, normalizovaná `candidate_data`, aplikovaná `data` a `extracted_fields.source_text` jsou samostatné kontrakty. Input nikdy nepoužívá evidence jako fallback. Re-extrakce je candidate, UI ukáže rozdíly a její explicitní aplikace vytvoří revizi i audit `AI_REEXTRACTION_APPLIED`.

## ADR-020: Approval zprostředkovává asynchronní Paperless upload

Queue manager nahrává PDF do Approval BFF, nikoli přímo do Paperless z browseru. Backend kontroluje soubor, počítá hash a jednorázově volá oficiální Paperless consume endpoint; worker sleduje vrácený task UUID do document ID a používá existující sync/AI pipeline. `document_uploads` obsahuje pouze tracking metadata a auditní korelaci, nikdy PDF. Per-file idempotency zabraňuje dvojímu odeslání při bezpečném retry; nejednoznačný transportní výsledek se automaticky neopakuje. Tato volba zachovává Paperless jako jediný document backend a eliminuje potřebu běžného přihlášení queue managera do Paperless UI.

## ADR-021: Fronta faktur je jediný trvalý uživatelský seznam

Historické `document_uploads` jsou technická diagnostika, nikoli druhá business fronta. Dashboard je proto při načtení nečte a zobrazuje pouze lokálně zahájenou dávku v kompaktním dočasném panelu. Jakmile refresh potvrdí vznik `invoice_id` v hlavní tabulce, tracking položka zmizí; chyba zůstává s retry a explicitním zavřením. Upload a refresh jsou ve společném action baru bez záporných marginů, aby desktopové zarovnání i responzivní zalomení vycházelo z jednoho layoutu.
