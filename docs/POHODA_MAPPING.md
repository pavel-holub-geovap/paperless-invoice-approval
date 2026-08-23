# Mapování přijaté faktury do POHODA XML 2.x

Mapování je odvozené z oficiálních `data.xsd`, `invoice.xsd`, `type.xsd` a dokumentace STORMWARE aktualizované 16. 10. 2025. Generátor vytváří výhradně `inv:invoiceType = receivedInvoice`; LLM se generování XML neúčastní.

| Approval field | POHODA XML | Zdroj | Povinnost | Transformace a validace |
|---|---|---|---|---|
| `supplier_name` | `inv:partnerIdentity/typ:address/typ:company` | schválená revize | povinné aplikací | max. délku ověří XSD |
| `supplier_address` | pouze zdrojový text, přímo se neexportuje | OCR/ruční kontrola | volitelné | generátor adresu z volného textu nehádá |
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
| `bank_account` + `bank_code` | `inv:paymentAccount/typ:accountNo`, `typ:bankCode` | schválená revize | společně volitelné | neúplná dvojice export zablokuje |
| `iban` + `swift_bic` | stejný `inv:paymentAccount` pár | schválená revize | společně volitelné | úplný IBAN+BIC má přednost před domácím párem; žádná hodnota se nedopočítává |
| `description` | `inv:invoiceHeader/inv:text` a prefix `inv:invoiceItem/inv:text` | schválená revize | povinné aplikací | položkový text je omezen na 90 znaků |
| `vat_lines[].vat_rate` | `inv:invoiceItem/inv:rateVAT` | schválená revize | povinné pro zdaněný doklad | `21 → high`, `12 → low`, `0 → none`; jiná sazba bez otestovaného mapování blokuje export |
| `vat_lines[].taxable_base` | `inv:invoiceItem/inv:homeCurrency/typ:price` | schválená revize | povinné aplikací | `Decimal`, 2 desetinná místa |
| `vat_lines[].vat_amount` | `.../typ:priceVAT` | schválená revize | povinné aplikací | `Decimal`, 2 desetinná místa |
| souhrny DPH | `inv:invoiceSummary/inv:homeCurrency/typ:price*` | schválený VAT breakdown | povinné aplikací pro CZK | sazby se agregují přesně, bez float |
| `total_amount` | součet všech položkových `typ:price + typ:priceVAT` a summary | schválená revize | povinné aplikací | musí souhlasit s allocations a VAT breakdown do 0,01 |
| `Allocation.amount` | hrubá částka jedné nebo více `inv:invoiceItem` | aktuální schválená revize | povinné | exportuje se allocation, nikoli approval assignment |
| `CostCenter.pohoda_code` | `inv:invoiceItem/inv:centre/typ:ids` | databázové středisko | povinné aplikací | prázdný kód blokuje export |
| `Allocation.vat_breakdown` | položky allocation podle sazeb | ručně potvrzené účetní rozdělení | podmíněně povinné | povinné pouze při kombinaci více středisek a více sazeb |

## Rozdělení DPH a rounding

- Jedno středisko: použijí se přesné VAT řádky faktury.
- Více středisek a jedna sazba: základ se rozdělí podle hrubých allocation částek metodou largest remainder na haléře; DPH každé allocation je `allocation gross − allocated base`. Tím se přesně zachová částka allocation, celkový základ, DPH i total.
- Jedno středisko a více sazeb: použijí se přesné VAT řádky faktury.
- Více středisek a více sazeb: automatický odhad je zakázán. Každá allocation musí mít explicitní `vat_breakdown`; součet po allocation i agregace po sazbě musí přesně rekonstruovat schválené hodnoty. Jinak export končí `MULTI_RATE_ALLOCATION_REQUIRES_EXPLICIT_VAT_SPLIT`.

Pro syntetickou fakturu s jedinou sazbou 21 % vzniknou přesně dvě účetní položky: 700 Kč se střediskem 200 a 510 Kč se střediskem 300. Dva schvalovatelé druhé allocation nevytvářejí druhou účetní položku.
