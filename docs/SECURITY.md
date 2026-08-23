# Bezpečnost

Secrets se načítají výhradně z prostředí nebo mountovaných secret souborů. `.env`, privátní klíče, databázové dumpy, modely a skutečné faktury jsou ignorované. Před commitem je nutná kontrola diffu na tokeny a hesla.

OIDC používá Authorization Code flow; aplikace drží session serverově a posílá pouze HttpOnly cookie. Role se validují na backendu. Paperless token je pouze serverový. Logovací filtry odstraňují Authorization/Cookie hlavičky a nikdy nelogují OCR či celé dokumenty.

Změnové operace vyžadují autentizaci, roli, kontrolu původu a audit. Exportní názvy jsou normalizované a cesty se neskládají z neupraveného uživatelského vstupu. XML parser zakazuje externí entity. Externí volání používají TLS, timeouty a omezené retry.

`QUEUE_MANAGER` může měnit faktury, střediska, rozúčtování a assignments. `APPROVER` získá pouze vlastní aktivní úkol aktuální revize a detail/PDF faktury s vlastním aktivním assignmentem; znalost cizího ID vede k HTTP 403. Backend znovu ověřuje aktivní Keycloak identitu a roli každého approvera při předání. `RETURN`/`REJECT` vyžadují komentář. Rozhodování používá databázové řádkové zámky a unikátní platné rozhodnutí, takže opakovaný nebo souběžný request nevytvoří dvě approvals.

Testovací Paperless má vlastní databázi, Redis a volumes. Žádná jeho služba nesmí dostat produkční Paperless credentials nebo mount. Approval backend používá pouze REST API token z runtime Docker volume a nemá Paperless DB heslo. Paperless OIDC client secret je jiný než secret Approval aplikace.

Na testovací HTTP/IP topologii zůstává běžné Paperless přihlášení zapnuté jako nouzová cesta, dokud není OIDC prakticky ověřeno. Prostředí nesmí být vystaveno do nedůvěryhodné sítě. Před produkčním použitím jsou povinné TLS, bezpečné hostname, rotace všech testovacích secrets a least-privileged Paperless service account.
