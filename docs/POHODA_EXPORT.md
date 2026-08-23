# POHODA export

Export je offline předání pro ruční import. Aplikace se nepřipojuje k POHODĚ, nespouští její desktop, nezapisuje do její databáze a nemění adresář ani číselníky.

## Stav a snapshot

První generování je povoleno pouze nad `APPROVED` aktuální revizí se všemi platnými approvals, potvrzeným originálem, bez blocking validation a s přesným součtem allocations. Vytvoří immutable `ExportArtifact` se snapshotem revize, allocations, approvals, verzí generátoru/XSD, XML/PDF hashem a výsledkem validace.

Úspěšná cesta je `APPROVED → XML_READY → READY_FOR_EXPORT → EXPORT_CREATED`. `IMPORTED_TO_POHODA` vznikne jen explicitní potvrzenou akcí správce a ukládá uživatele, čas a konkrétní export ID. Stažení souboru stav nemění.

Re-export je povolen jen pro stejnou aktuální revizi. Vytvoří nový artifact s odkazem na původní export a audit `REEXPORTED`; změněná revize musí znovu projít schválením.

## Soubory

- XML: immutable Windows-1250 artifact, vždy XSD-validní.
- PDF: při generování se pouze načte z Paperless REST API pro hash; samostatné stažení používá chráněný proxy endpoint.
- ZIP: archivovaný exportní artifact smí obsahovat PDF a XML. Batch má stabilní složky `invoice-<bezpečné číslo>/invoice.xml|invoice.pdf` a vlastní SHA-256.

Při tvorbě ZIP se PDF znovu načte z Paperless a jeho hash musí odpovídat exportnímu snapshotu. Tím se zabrání spojení XML s mezitím změněným originálem.

## POHODA response

Správce může nahrát nejvýše 5 MiB response XML. Bezpečný parser ověří `responsePack`, XSD a vypíše stav každého `responsePackItem`, `producedDetails` a `importDetails/detail`. Upload je diagnostický a automaticky nemění fakturu na `IMPORTED_TO_POHODA`.

## Duplicity

Před generováním se porovnává IČO dodavatele, dodavatelské číslo faktury, total a datum vystavení. Shoda vytvoří WARNING `POSSIBLE_DUPLICATE_INVOICE`; záznam se nemaže ani neslučuje.
