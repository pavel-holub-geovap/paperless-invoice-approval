# Schvalovací workflow

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

Úspěch provede `READY_FOR_APPROVAL → AWAITING_APPROVAL`, zapíše `SENT_FOR_APPROVAL` a zařadí idempotentní synchronizaci Paperless tagu.

## Rozhodnutí

- `APPROVE` mění pouze vlastní assignment. Faktura zůstává `AWAITING_APPROVAL`, dokud všechny aktivní povinné assignmenty aktuální revize nejsou `APPROVED`.
- `RETURN` vyžaduje komentář a přepne celou fakturu do `RETURNED`. Opětovné předání vytvoří novou revizi.
- `REJECT` vyžaduje komentář a okamžitě přepne celou fakturu do `REJECTED`; další rozhodnutí jsou odmítnuta.
- `REOPEN` smí provést jen správce nad `REJECTED`. Vytvoří novou revizi, zruší potvrzení originálu a zachová invalidované rejection rozhodnutí.

## Revize a invalidace

Po zahájení schvalování změna dodavatele, identifikátorů, čísel/datem/částek, DPH, platebních údajů, měny, allocation částky/procenta/střediska nebo approvera vytvoří novou revizi. Staré decisions dostanou `valid=false`, čas a důvod invalidace; staré assignmenty dostanou `INVALIDATED`, `active=false`, čas a důvod. Nic se nemaže. Audit obsahuje `REVISION_CREATED` a `APPROVAL_INVALIDATED` s ID dotčených záznamů.

## Souběh a idempotence

Rozhodnutí zamyká nejprve fakturu a potom assignment pomocí `SELECT ... FOR UPDATE`. Částečný unikátní index dovolí jeden platný decision na assignment. Opakovaný shodný request vrátí existující decision; jiná akce, stará revize, invalidovaný assignment nebo faktura mimo `AWAITING_APPROVAL` vrátí konflikt.

## Přístup

Správce vidí celou frontu, spravuje střediska/data/allocations/approvery, potvrzuje originál, předává a znovu otevírá. Approver vidí pouze vlastní pending assignments aktuální revize ve faktuře `AWAITING_APPROVAL`, jejich fakturační údaje a PDF. Manažerské API a cizí assignment jsou HTTP 403.

## Paperless tagy

`QUEUE_REVIEW`, `NEEDS_REVIEW` a `RETURNED` používají tag kontroly správce; `AWAITING_APPROVAL` tag ke schválení; `APPROVED` tag schváleno; `REJECTED` tag zamítnuto. Mapování je konfigurovatelné. Business transakce se commitne před externím voláním; selhání zůstane v databázovém jobu s bounded retry.

## Zobrazení a živá konzistence

Detail skládá deterministický stepper `DONE / CURRENT / WAITING / BLOCKED / ERROR` ze stavu zdroje, validací, rozúčtování, kontroly originálu, assignments, schválení, exportu a potvrzení importu. Každý krok uvádí další akci nebo důvod blokace. Změna z jiné relace se projeví pollingem; při rozepsaném formuláři pouze vznikne upozornění a explicitní volba načíst novou revizi.
