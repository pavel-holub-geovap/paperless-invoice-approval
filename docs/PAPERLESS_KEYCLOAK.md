# Přihlášení existujícího Paperless přes Keycloak

Tento projekt existující Paperless automaticky nemění. Následující konfiguraci aplikujte až po ověření jeho verze, veřejné URL a po záloze. Příklad odpovídá aktuálnímu Paperless-ngx s providerem `django-allauth` OpenID Connect.

## Keycloak client `paperless`

- Realm/issuer: `${KEYCLOAK_PUBLIC_URL}/realms/${KEYCLOAK_REALM}`
- Client ID: `paperless`
- Typ: confidential, Authorization Code flow zapnutý, Direct Access Grants vypnuté
- Scopes: `openid profile email groups`
- Přesná callback URL: `${PAPERLESS_URL}/accounts/oidc/keycloak/login/callback/`
- Web origin: přesná hodnota `${PAPERLESS_URL}`
- Post logout redirect: `${PAPERLESS_URL}/*`, pouze pokud je skutečně potřeba

`keycloak` v callback URL je `provider_id` z konfigurace níže. Trailing slash je součástí přesné URL. V Keycloak přidejte protocol mapper typu Group Membership, token claim `groups`, do ID tokenu i userinfo; plnou cestu skupiny vypněte, pokud názvy skupin mají přímo odpovídat Paperless.

## Environment konfigurace Paperless

```text
PAPERLESS_URL=https://paperless.example.cz
PAPERLESS_APPS=allauth.socialaccount.providers.openid_connect
PAPERLESS_SOCIALACCOUNT_PROVIDERS={"openid_connect":{"SCOPE":["openid","profile","email","groups"],"APPS":[{"provider_id":"keycloak","name":"Keycloak","client_id":"paperless","secret":"<PAPERLESS_OIDC_CLIENT_SECRET>","settings":{"server_url":"https://keycloak.example.cz/realms/paperless-invoice/.well-known/openid-configuration","token_auth_method":"client_secret_basic"}}]}}
PAPERLESS_SOCIAL_AUTO_SIGNUP=true
PAPERLESS_SOCIALACCOUNT_ALLOW_SIGNUPS=true
PAPERLESS_SOCIAL_ACCOUNT_SYNC_GROUPS=true
PAPERLESS_SOCIAL_ACCOUNT_SYNC_GROUPS_CLAIM=groups
```

Secret uložte pouze do secret manageru nebo lokálního `.env` existujícího Paperless, nikdy do tohoto repozitáře. `PAPERLESS_OAUTH_CALLBACK_BASE_URL` nepoužívejte pro SSO; podle dokumentace platí pro OAuth e-mailů. Reverse proxy musí zachovat veřejný `Host` a `X-Forwarded-Proto`, aby Paperless sestavil veřejnou callback URL.

## Role/skupiny

V Keycloak vytvořte skupiny, které již existují v Paperless, například `PaperlessUsers` a `PaperlessManagers`. Aplikační client roles `QUEUE_MANAGER` a `APPROVER` automaticky neudělují Paperless administraci. Oprávnění k dokumentům nastavte v Paperless na jeho skupinách a synchronizujte pouze explicitně schválené názvy.

## Ověření

1. Nejdřív ponechte běžné Paperless přihlášení zapnuté jako nouzovou cestu.
2. Otevřete OIDC login, ověřte issuer, callback a že browser posílá veřejnou URL.
3. Přihlaste každého typu testovacího uživatele a ověřte username/e-mail/skupiny i oprávnění k dokumentům.
4. Ověřte logout, odmítnutí neznámého redirect URI a přihlášení uživatele bez povolené skupiny.
5. Teprve poté případně nastavte `PAPERLESS_DISABLE_REGULAR_LOGIN=true` nebo `PAPERLESS_REDIRECT_LOGIN_TO_SSO=true`.
