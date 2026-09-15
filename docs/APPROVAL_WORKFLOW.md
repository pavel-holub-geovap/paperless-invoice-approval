# Schvalovací workflow

## Doklad nahraný schvalovatelem

Schvalovatel nahraje PDF přes stejnou Paperless pipeline, po dokončení OCR/extrakce zvolí typ dokladu a `payment_required`, zkontroluje data včetně `rounding_amount` a rozdělí částku do libovolných aktivních sekcí. Section permission není filtr allocation; určuje pouze právo schválit danou sekci.

Teprve při `submit-for-review` backend znovu načte aktuální `ApproverSectionPermission`. Pro každou uploaderovu právě oprávněnou sekci vytvoří standardní `ApprovalAssignment` a platný `ApprovalDecision(APPROVE)` s auditní provenance `UPLOADER_ACTIVE_SECTION_PERMISSION_AT_SUBMIT`. Neoprávněná allocation zůstane beze schválení a queue manager jí přiřadí běžného oprávněného approvera.

Ani úplné automatické schválení uploaderových částí nevytvoří finální stav nebo schválené PDF. Queue manager ověří originál, klasifikaci, K zaplacení, zaokrouhlení, sekce, poznámky a approvery a review potvrdí při předání do standardního approval workflow. Každá významná změna po předání vytvoří novou revizi bez předchozí review značky; dřívější auto-approval zůstává v historii jako invalidovaný.

Workflow stav je nezávislý na `Invoice.disposition` a `source_status`. Ignorování ani Paperless 404 stav nepřepisují; ignored/MISSING pouze zablokují nové předání, rozhodnutí, export a potvrzení importu. Obnovení zdroje/dispozice pokračuje z dochovaného workflow podle jeho běžných preconditions.

## Doménová vazba

Schvalovatel nerozhoduje o faktuře obecně. Assignment vždy váže konkrétní fakturu, `InvoiceRevision`, allocation, středisko, peněžní částku a approvera. Allocation částky jsou `Decimal`/PostgreSQL `NUMERIC`; procento je vstup a provenance, autoritativní je vypočtená částka.

## Předání

Backend při každém `submit` znovu spustí validace a ověří:

1. revize obsahuje AI nebo ručně doplněná data;
2. správce explicitně potvrdil kontrolu originálního PDF;
3. neexistuje `BLOCKING_ERROR`;
4. existuje alespoň jedna allocation a součet se od total_amount liší nejvýše o 0,01;
5. každá allocation má povinného approvera;
6. každý approver je aktivní uložená identita s rolí `APPROVER`.
7. `payment_required` je explicitně `true` nebo `false`; legacy `NULL` workflow blokuje.

Úspěch provede `READY_FOR_APPROVAL → AWAITING_APPROVAL`, zapíše `SENT_FOR_APPROVAL` a zařadí idempotentní synchronizaci Paperless tagu.

## Rozhodnutí

- `APPROVE` mění pouze vlastní assignment. Faktura zůstává `AWAITING_APPROVAL`, dokud všechny aktivní povinné assignmenty aktuální revize nejsou `APPROVED`.
- `RETURN` vyžaduje komentář a přepne celou fakturu do `RETURNED`. Opětovné předání vytvoří novou revizi.
- `REJECT` vyžaduje komentář a okamžitě přepne celou fakturu do `REJECTED`; další rozhodnutí jsou odmítnuta.
- `REOPEN` smí provést jen správce nad `REJECTED`. Vytvoří novou revizi, zruší potvrzení originálu a zachová invalidované rejection rozhodnutí.

## Revize a invalidace

Po zahájení schvalování změna dodavatele, identifikátorů, čísel/datem/částek, DPH, `payment_required`, `rounding_amount`, platebních údajů, měny, allocation částky/procenta/střediska/poznámky nebo approvera vytvoří novou revizi. Staré decisions dostanou `valid=false`, čas a důvod invalidace; staré assignmenty dostanou `INVALIDATED`, `active=false`, čas a důvod. Nic se nemaže. Audit obsahuje `REVISION_CREATED` a `APPROVAL_INVALIDATED` s ID dotčených záznamů.

## Souběh a idempotence

Rozhodnutí zamyká nejprve fakturu a potom assignment pomocí `SELECT ... FOR UPDATE`. Částečný unikátní index dovolí jeden platný decision na assignment. Opakovaný shodný request vrátí existující decision; jiná akce, stará revize, invalidovaný assignment nebo faktura mimo `AWAITING_APPROVAL` vrátí konflikt.

## Přístup

Správce vidí celou frontu, spravuje střediska/data/allocations/approvery, potvrzuje originál, předává a znovu otevírá. Approver vidí pouze vlastní pending assignments aktuální revize ve faktuře `AWAITING_APPROVAL`, jejich fakturační údaje a PDF. Manažerské API a cizí assignment jsou HTTP 403.

## Paperless tagy

`QUEUE_REVIEW`, `NEEDS_REVIEW` a `RETURNED` používají tag kontroly správce; `AWAITING_APPROVAL` tag ke schválení; `APPROVED` tag schváleno; `REJECTED` tag zamítnuto. Mapování je konfigurovatelné. Business transakce se commitne před externím voláním; selhání zůstane v databázovém jobu s bounded retry.

## Zobrazení a živá konzistence

Detail skládá deterministický stepper `DONE / CURRENT / WAITING / BLOCKED / ERROR` ze stavu zdroje, validací, rozúčtování, kontroly originálu, assignments, schválení, exportu a potvrzení importu. Každý krok uvádí další akci nebo důvod blokace. Změna z jiné relace se projeví pollingem; při rozepsaném formuláři pouze vznikne upozornění a explicitní volba načíst novou revizi.
