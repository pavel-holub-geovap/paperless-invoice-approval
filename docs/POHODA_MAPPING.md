# Mapování POHODA XML

Generátor vytváří přijatou fakturu deterministicky ze schválené revize. `CostCenter.pohoda_code` se propisuje do účetních částí. Decimal částky a DPH se nepřepočítávají pomocí LLM.

Konkrétní namespace, pořadí elementů a způsob rozdělení středisek odpovídají sadě XSD uložené ve `fixtures/pohoda`. Každý export prochází validátorem celého `dataPack`; při chybě se stav `XML_READY` nenastaví.

## Použitá sada XSD

- Zdroj: `https://www.stormware.cz/schema/version_2/`, staženo 2026-08-20 rekurzivně podle `schemaLocation` pomocí `scripts/download_pohoda_xsd.ps1`.
- Namespace/verze formátu: `http://www.stormware.cz/schema/version_2/*`, `dataPack` 2.0, `dataPackItem` 2.0, `invoice` 2.0.
- Počet souborů v uzavřeném grafu závislostí: 73.
- SHA-256 `data.xsd`: `207F1206BEB41438A43272A6FFFEADD9C7039B2F7C7697A3252A4176109BE9D9`.
- SHA-256 `invoice.xsd`: `5DCB68CDAF8C2CFD15C9D84C3BCDFB5704B666D58D125B42B76783B628F3F014`.
- Aktualizace: znovu spusťte downloader, zkontrolujte diff a hash, spusťte XML regresní testy a otestujte import v neprodukční účetní jednotce POHODY. Aktualizaci schémat nikdy nekombinujte s automatickým importem.

Aktuální implementační volba: pro každou allocation a sazbu DPH vznikne samostatná textová položka s `centre/ids = pohoda_code`. Základ i DPH se mezi střediska rozdělí metodou largest remainder po haléřích; součet přesně zachová originální DPH rozpad. Mapování sazeb je `21 → high`, `12 → low`, `0 → none`; jiná sazba vyvolá explicitní chybu, dokud nebude doplněn otestovaný historický režim. Cizí měna vyžaduje ručně zkontrolovaný `exchange_rate`. Pokud cílová verze POHODY vyžaduje jiné členění, změna musí dostat fixture z POHODY, regresní test a záznam v tomto dokumentu. Nejasnost se nesmí zakrýt náhodným dopočtem.
