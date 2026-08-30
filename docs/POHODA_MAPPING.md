# Mapování přijaté faktury do POHODA XML 2.x

Mapování je odvozené z oficiálních `data.xsd`, `invoice.xsd`, `type.xsd` a dokumentace STORMWARE aktualizované 16. 10. 2025. Generátor vytváří výhradně `inv:invoiceType = receivedInvoice`; LLM se generování XML neúčastní.

| Approval field | POHODA XML | Zdroj | Povinnost | Transformace a validace |
|---|---|---|---|---|
| `supplier_name` | `inv:partnerIdentity/typ:address/typ:company` | schválená revize | povinné aplikací | max. délku ověří XSD |
| `supplier_address_raw` | pouze zdrojový text, přímo se neexportuje | dodavatelský blok OCR/ruční kontrola | volitelné | PSČ se normalizuje; generátor adresu z volného textu nehádá |
| `supplier_street` | `.../typ:street` | ručně potvrzená strukturovaná adresa | povinné aplikací | beze změny |
| `supplier_city` | `.../typ:city` | ručně potvrzená strukturovaná adresa | povinné aplikací | beze změny |
| `supplier_zip` | `.../typ:zip` | ručně potvrzená strukturovaná adresa | povinné aplikací | beze změny |
| `supplier_ico` | `.../typ:ico` | schválená revize | volitelné XSD | beze změny, deterministická validace IČO |
| `supplier_dic` | `.../typ:dic` | schválená revize | volitelné XSD | beze změny |
| — | atribut `typ:address/@linkToAddress` | konfigurace generátoru | explicitní | vždy `false`; nevzniká vazba na Adresář POHODY |
| `invoice_number` | `inv:invoiceHeader/inv:originalDocument` | dodavatelské číslo faktury | povinné aplikací | nevkládá se do `inv:number`; interní číselnou řadu určí POHODA |
| `variable_symbol` | `inv:invoiceHeader/inv:symVar` | schválená revize | volitelné | max. 20 znaků dle XSD |
| `issue_date` | `inv:invoiceHeader/inv:date` | schválená revize | povinné aplikací | ISO `YYYY-MM-DD` |
| `taxable_supply_date` | `inv:invoiceHeader/inv:dateTax` | schválená revize | volitelné XSD | ISO `YYYY-MM-DD` |
| `due_date` | `inv:invoiceHeader/inv:dateDue` | schválená revize | volitelné XSD | ISO `YYYY-MM-DD` |
| `currency` | `inv:homeCurrency`, nebo `inv:foreignCurrency/typ:currency/typ:ids` | schválená revize | povinné aplikací | CZK používá home currency; cizí měna vyžaduje ručně ověřený kurz |
| normalizované `bank_account` + `bank_code` | `inv:paymentAccount/typ:accountNo`, `typ:bankCode` | schválená revize | společně volitelné | vstup `[prefix-]number/code` se rozloží; `accountNo` nikdy neobsahuje lomítko/kód a neúplná dvojice export zablokuje |
| `iban` + `swift_bic` | stejný `inv:paymentAccount` pár | schválená revize | společně volitelné | úplný IBAN+BIC má přednost před domácím párem; žádná hodnota se nedopočítává |
| `description` | `inv:invoiceHeader/inv:text` a prefix `inv:invoiceItem/inv:text` | schválená revize | povinné aplikací | položkový text je omezen na 90 znaků |
| `vat_lines[].vat_rate` | `inv:invoiceItem/inv:rateVAT` | schválená revize | povinné pro zdaněný doklad | `21 → high`, `12 → low`, `0 → none`; jiná sazba bez otestovaného mapování blokuje export |
| `vat_lines[].taxable_base` | `inv:invoiceItem/inv:homeCurrency/typ:price` | schválená revize | povinné aplikací | `Decimal`, 2 desetinná místa |
| `vat_lines[].vat_amount` | `.../typ:priceVAT` | schválená revize | povinné aplikací | `Decimal`, 2 desetinná místa |
| `vat_lines[].adjustment_type=ROUNDING` | agreguje se do stejné sazby v položkách a summary | explicitní řádek faktury | volitelné | základ, DPH a hrubá korekce se zachovají; nerozbíjí jednu sazbu na falešné více-sazbové rozhodnutí |
| souhrny DPH | `inv:invoiceSummary/inv:homeCurrency/typ:price*` | schválený VAT breakdown | povinné aplikací pro CZK | sazby se agregují přesně, bez float |
| `total_amount` | součet položek a summary | schválená revize | povinné aplikací | nesmí být vymyšleno z allocations |
| `invoice_items[]` | `inv:invoiceDetail/inv:invoiceItem` | skutečně vytěžené položky | volitelné | mají přednost před DPH summary fallbackem; bez `inv:centre` |
| `Allocation.amount` + středisko | `inv:invoiceHeader/inv:text` | aktuální schválená revize | informativní | český deterministický summary; nejde o finální zaúčtování |
| `CostCenter.pohoda_code` | nikam do účetních položek | Approval workflow | — | Approval nikdy nevydává interní středisko za účetní středisko POHODY |

## Rozdělení DPH a rounding

- Jsou-li dostupné skutečné `invoice_items`, exportují se jejich popisy, množství, ceny a sazby.
- Jinak vzniknou položky z vytěžených DPH souhrnů. Neobsahují předkontaci, účet ani `inv:centre`.
- Allocations zůstávají ve schvalovacím PDF, auditu a informativním textu. Finální účetní rozúčtování provádí účetní firma.
- Samostatné, deterministicky ověřené zaokrouhlení ve stejné sazbě se před rozdělením do POHODY agreguje s hlavním VAT řádkem. Neověřená klasifikace LLM se do snapshotu ani XML nedostane. Deklarované součty z faktury se nedopočítávají ani nepřepisují; reconciliation odchylka je WARNING.

Pro syntetickou fakturu se souhrnem 1 000 + 210 Kč vznikne jeden DPH řádek bez střediska. Allocation 700/510 Kč se objeví pouze v poznámce a schváleném PDF.

## Identifikace účetní jednotky

`dat:dataPack/@ico` se plní pouze z `POHODA_TARGET_ICO` a identifikuje účetní jednotku, do které se balíček importuje. `supplier_ico` se mapuje pouze do `inv:partnerIdentity/typ:address/typ:ico`. Tyto hodnoty se v testech záměrně liší. `dat:dataPack/@key` vznikne pouze při explicitním `POHODA_TARGET_KEY`; generátor žádný náhodný klíč nevytváří.

Regresní test parsuje skutečně serializované XML a používá rozdílné hodnoty `dataPack/@ico=15049248` a `partnerIdentity/address/ico=28652240`. Vedle XSD se výsledek označí samostatně jako `TARGET_UNIT_VALID`; bez tohoto výsledku nelze považovat cílovou jednotku za ověřenou.
