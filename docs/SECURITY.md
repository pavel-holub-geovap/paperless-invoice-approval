# Bezpečnost

Secrets se načítají výhradně z prostředí nebo mountovaných secret souborů. `.env`, privátní klíče, databázové dumpy, modely a skutečné faktury jsou ignorované. Před commitem je nutná kontrola diffu na tokeny a hesla.

OIDC používá Authorization Code flow; aplikace drží session serverově a posílá pouze HttpOnly cookie. Role se validují na backendu. Paperless token je pouze serverový. Logovací filtry odstraňují Authorization/Cookie hlavičky a nikdy nelogují OCR či celé dokumenty.

Změnové operace vyžadují autentizaci, roli, kontrolu původu a audit. Exportní názvy jsou normalizované, resolved cesta musí zůstat v exportním rootu a XSD-invalidní XML nelze stáhnout. XML parser i XSD validátor zakazují síť, externí entity a huge-tree režim; response upload má limit 5 MiB a je pouze diagnostický. Externí volání používají TLS, timeouty a omezené retry.

Immutable export ukládá jen účetní snapshot, XML a hashe. Originální PDF se při generování načte z Paperless pouze pro SHA-256 a trvale se kopíruje jen do explicitně vytvořeného exportního ZIPu, nikoli do Approval DB. Před tvorbou ZIPu se PDF načte znovu a hash musí odpovídat snapshotu. LLM nemá přístup do XML generátoru a nesmí určovat střediska ani VAT split.

`QUEUE_MANAGER` může měnit faktury, střediska, rozúčtování a assignments. `APPROVER` získá pouze vlastní aktivní úkol aktuální revize a detail/PDF faktury s vlastním aktivním assignmentem; znalost cizího ID vede k HTTP 403. Backend znovu ověřuje aktivní Keycloak identitu a roli každého approvera při předání. `RETURN`/`REJECT` vyžadují komentář. Rozhodování používá databázové řádkové zámky a unikátní platné rozhodnutí, takže opakovaný nebo souběžný request nevytvoří dvě approvals.

Testovací Paperless má vlastní databázi, Redis a volumes. Žádná jeho služba nesmí dostat produkční Paperless credentials nebo mount. Approval backend používá pouze REST API token z runtime Docker volume a nemá Paperless DB heslo. Paperless OIDC client secret je jiný než secret Approval aplikace.

Reconciliation nesmí zaměnit výpadek za smazání: pouze konkrétní HTTP 404 nastaví `MISSING`. Chybějící zdroj blokuje další approval, nový export/import, PDF a ZIP, ale nemaže důkazní historii. Disposition mění pouze `QUEUE_MANAGER`; approver dostane HTTP 403. Paperless tag se zapisuje jen existujícímu dokumentu a automatické fyzické mazání není součástí aplikace.

Correction smoke smí mazat pouze dvě syntetická Paperless ID, která sám právě vytvořil a zaznamenal. Cleanup nevyhledává ani nemaže uživatelské faktury. Report neobsahuje PDF bytes, OCR text, token ani heslo.

Na testovací HTTP/IP topologii zůstává běžné Paperless přihlášení zapnuté jako nouzová cesta, dokud není OIDC prakticky ověřeno. Prostředí nesmí být vystaveno do nedůvěryhodné sítě. Před produkčním použitím jsou povinné TLS, bezpečné hostname, rotace všech testovacích secrets a least-privileged Paperless service account.

## Korelace a bezpečnostně významné akce

Každý HTTP požadavek dostává `X-Request-ID`; klientský identifikátor lze převzít, jinak vznikne UUID. Nové auditní události ukládají korelační ID a dostupné OIDC údaje actor subject, username a role. Stažení originálního PDF, POHODA XML a exportního ZIPu je auditováno. Paperless token ani obsah PDF se do auditních metadata neukládá.

`POHODA_TARGET_ICO` a volitelný `POHODA_TARGET_KEY` jsou pouze serverová konfigurace. `.env.example` obsahuje prázdné hodnoty a skutečná konfigurace nesmí být commitována.

Upload PDF je BFF operace pouze pro `QUEUE_MANAGER`; role a CSRF jsou vynuceny backendem a Paperless token se neposílá browseru. Backend ověřuje příponu, MIME, `%PDF-` magic a `UPLOAD_MAX_BYTES`, odstraňuje control/path separátory z názvu a nikdy z něj nekonstruuje lokální filesystem cestu. Dočasný multipart obsah žije pouze po dobu requestu, po předání Paperless se neukládá do DB ani auditu. SHA-256 slouží k diagnostice a přesné duplicate indikaci, nikoli jako automatický důvod ke smazání.

Retry je povolen jen při prokazatelném connect failure před přijetím uploadu. Timeout, přerušené spojení po odeslání a Paperless 5xx mají stav `SUBMISSION_UNKNOWN`; systém je automaticky neopakuje, protože Paperless upload API nemá Approval idempotency key a první consume task mohl vzniknout.
