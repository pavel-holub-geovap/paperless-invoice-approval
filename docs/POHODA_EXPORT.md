# POHODA export

Export je offline předání pro ruční import. Aplikace se nepřipojuje k POHODĚ, nespouští její desktop, nezapisuje do její databáze a nemění adresář ani číselníky.

## Stav a snapshot

POHODA se týká pouze `RECEIVED_INVOICE`. S validním ISDOC je metoda `PDF_ISDOC` a importním podkladem je uložená schválená kopie se shodným ISDOC hashem; generované invoice XML je zakázáno. Bez validního ISDOC je metoda `GENERATED_XML` a první generování je povoleno pouze nad `APPROVED` aktuální revizí se všemi platnými approvals, potvrzeným originálem, bez blocking validation a s přesným součtem allocations.

XML cesta je `APPROVED → XML_READY → READY_FOR_EXPORT → EXPORT_CREATED`. PDF+ISDOC cesta přejde po bezpečném uložení kopie `APPROVED → READY_FOR_EXPORT → EXPORT_CREATED`. `IMPORTED_TO_POHODA` vznikne u obou metod jen explicitní potvrzenou akcí správce. `RECEIVED_ADVANCE_INVOICE` a ostatní typy mají metodu `NONE`; export, batch i potvrzení importu jsou backendem zakázané.

Re-export je povolen jen pro stejnou aktuální revizi. Vytvoří nový artifact s odkazem na původní export a audit `REEXPORTED`; změněná revize musí znovu projít schválením.

Ignorovaná faktura nebo faktura s `source_status=MISSING` nesmí vytvořit první export ani re-export a nesmí být nově označena jako importovaná. Historický XML artifact zůstává dostupný jako immutable evidence; `MISSING` ale blokuje PDF a ZIP, protože nelze ověřit aktuální autoritativní originál.

## Soubory

- XML: immutable Windows-1250 artifact, vždy XSD-validní.
- PDF: při generování se pouze načte z Paperless REST API pro hash; samostatné stažení používá chráněný proxy endpoint.
- ZIP: archivovaný exportní artifact smí obsahovat PDF a XML. Batch má stabilní složky `invoice-<bezpečné číslo>/invoice.xml|invoice.pdf` a vlastní SHA-256.

## Položky a allocations

Approval allocations vyjadřují věcné schválení nákladu, nikoli účetní zaúčtování. Generátor proto nevytváří `inv:centre` ani položky podle allocations. Pokud jsou dostupné skutečné vytěžené `invoice_items`, použije je; jinak vytvoří XSD-validní DPH souhrnné řádky bez vymyšlené účetní analytiky. Allocation summary je deterministicky pouze v `invoiceHeader/text` spolu s větou, že finální účetní rozúčtování provádí účetní.

Při tvorbě ZIP se PDF znovu načte z Paperless a jeho hash musí odpovídat exportnímu snapshotu. Tím se zabrání spojení XML s mezitím změněným originálem.

## POHODA response

Správce může nahrát nejvýše 5 MiB response XML. Bezpečný parser ověří `responsePack`, XSD a vypíše stav každého `responsePackItem`, `producedDetails` a `importDetails/detail`. Upload je diagnostický a automaticky nemění fakturu na `IMPORTED_TO_POHODA`.

## Duplicity

Před generováním se porovnává IČO dodavatele, dodavatelské číslo faktury, total a datum vystavení. Shoda vytvoří WARNING `POSSIBLE_DUPLICATE_INVOICE`; záznam se nemaže ani neslučuje.

## Platební účet ve verzi v3

Generátor `pohoda-received-invoice.v3` před mapováním znovu deterministicky normalizuje data. Domácí účet `[prefix-]number/bank_code` se zapíše jako `typ:accountNo=[prefix-]number` a `typ:bankCode=bank_code`; lomítko ani celý kombinovaný řetězec se nikdy nedostane do obou elementů. IBAN/BIC zůstávají samostatnou podporovanou dvojicí podle existujícího mapovacího kontraktu. Každé nové generování vytvoří nový immutable artifact; starší artifact se nepřepisuje.

Export fail-fast vyžaduje cílové IČO. Snapshot artefaktu ukládá `pohoda_target_ico` a informaci, zda byl nakonfigurován key. Historické chybné artefakty zůstávají beze změny kvůli auditní stopě; oprava vždy vytváří nový immutable artefakt.

XSD validace sama neurčuje cílovou účetní jednotku, protože `dat:dataPack/@ico` je ve schématu volitelné. Nad finálními serializovanými bytes proto probíhá samostatná kontrola `TARGET_UNIT_VALID`: root musí být `dat:dataPack`, `@ico` se musí přesně rovnat `POHODA_TARGET_ICO` a `@key` musí chybět, není-li explicitně nakonfigurován. Stejná kontrola a SHA-256 se opakují při stažení i při vložení do ZIP. Snapshot konfigurace není důkazem obsahu XML.
