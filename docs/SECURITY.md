# Bezpečnost

Secrets se načítají výhradně z prostředí nebo mountovaných secret souborů. `.env`, privátní klíče, databázové dumpy, modely a skutečné faktury jsou ignorované. Před commitem je nutná kontrola diffu na tokeny a hesla.

OIDC používá Authorization Code flow; aplikace drží session serverově a posílá pouze HttpOnly cookie. Role se validují na backendu. Paperless token je pouze serverový. Logovací filtry odstraňují Authorization/Cookie hlavičky a nikdy nelogují OCR či celé dokumenty.

Změnové operace vyžadují autentizaci, roli, kontrolu původu a audit. Exportní názvy jsou normalizované a cesty se neskládají z neupraveného uživatelského vstupu. XML parser zakazuje externí entity. Externí volání používají TLS, timeouty a omezené retry.

