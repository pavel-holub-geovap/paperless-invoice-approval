# POHODA XSD bundle 2.x

- Aktivní verze: `2025-10-16` (viz `CURRENT`).
- Datum stažení: 2026-08-23.
- Autoritativní přehled a datum vydání: `https://www.stormware.cz/pohoda/xml/seznamschemat/`.
- Oficiální archiv: `https://www.stormware.cz/xml/schema/all_schema_ver2.zip`.
- Archiv má 183 047 B a SHA-256 `ab6a9f3c406a9e2257f544203d21df3723e8e10026e73a0898aa6249446bfd9b`.
- Rozbalený bundle obsahuje 75 XSD souborů. Produkční validace používá lokální `2025-10-16/data.xsd`; žádné schéma se při exportu nestahuje.

Hlavní checksumy:

- `data.xsd`: `207f1206beb41438a43272a6fffeadd9c7039b2f7c7697a3252a4176109be9d9`
- `invoice.xsd`: `5dcb68cdaf8c2cfd15c9d84c3bcdfb5704b666d58d125b42b76783b628f3f014`
- `type.xsd`: `4adacd620bc132c6efa90dc4e203442120aa43f6de83482ea138cb7012bc70de`
- `response.xsd`: `546312a5db5e23b036f9d762b736464c09727e0eb94e02250928b1f8c04f83d4`
- `documentresponse.xsd`: `1f12df2295a51dd08ed7ed9d1f26dc34dd255bd2edaa4d5c9faf305a18948354`
- `list.xsd`: `9c7f32286051b5ef819ffd2c9b8896be178882ced90b0a2b77db56d769d45a13`

Adresář `samples` obsahuje request/response stažený z odkazu, který aktuální stránka STORMWARE označuje jako vzor přijaté faktury. Samotný publikovaný request však v době stažení obsahoval `issuedInvoice`; proto neslouží jako golden vstup pro náš `receivedInvoice`. Response se používá jen pro obecný parser `responsePack`. Golden `receivedInvoice` generují testy z aktuálního XSD.
