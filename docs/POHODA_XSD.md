# POHODA XSD

Aktivní sada je úplný oficiální bundle POHODA XML 2.x, který STORMWARE uvádí jako aktualizovaný 16. 10. 2025. Stažen byl 23. 8. 2026 z `https://www.stormware.cz/xml/schema/all_schema_ver2.zip` a je uložen v `schemas/pohoda/2025-10-16`.

Produkční validace začíná v `data.xsd`, které transitivně načte schémata jednotlivých agend. Pro Etapu F jsou hlavní `data.xsd`, `invoice.xsd`, `type.xsd`, `response.xsd`, `documentresponse.xsd` a `list.xsd`. Bundle má 75 XSD a jeho archiv má SHA-256 `ab6a9f3c406a9e2257f544203d21df3723e8e10026e73a0898aa6249446bfd9b`. Další checksumy jsou v `schemas/pohoda/README.md`.

Validátor používá `lxml.etree.XMLSchema`, zakazuje síť, externí entity a huge-tree režim. Chyby se ukládají jako line, column, schema message a XPath. XML se uživateli zpřístupní pouze při `XSD_VALID`.

Aktualizace bundle je samostatná kontrolovaná změna: stáhnout oficiální archiv, vytvořit nový datovaný adresář, aktualizovat `CURRENT`, checksumy, Docker image a regresní testy. Schémata se za běhu nikdy nestahují.

XSD validita sama nepotvrzuje praktickou kompatibilitu. První artifact pro dokument 1 musí být ručně importován do testovací účetní jednotky POHODY a následně porovnán se STORMWARE response XML a kontrolním exportem.
