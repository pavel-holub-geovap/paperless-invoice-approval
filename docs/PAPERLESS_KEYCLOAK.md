# Společné přihlášení Paperless a Approval přes Keycloak

Keycloak realm `paperless-invoice` je společný identity provider, ale každá aplikace má samostatný confidential client a samostatný secret.

## Clients a callback URI

| Client | Veřejná URL | Redirect URI |
| --- | --- | --- |
| `approval-app` | `APP_BASE_URL` | `${APP_BASE_URL}/api/auth/callback` |
| `paperless` | `PAPERLESS_PUBLIC_URL` | `${PAPERLESS_PUBLIC_URL}/accounts/oidc/keycloak/login/callback/` |

Provider ID je `keycloak`; trailing slash callbacku Paperless je záměrný. Issuer je `${KEYCLOAK_PUBLIC_URL}/realms/${KEYCLOAK_REALM}`. Reverse proxy zachovává `Host`, `X-Forwarded-Host`, port a protokol.

`keycloak-provision` idempotentně vytvoří realm, oba clients, realm roles a skupiny `QUEUE_MANAGER`/`APPROVER`, group claim mapper a čtyři testovací uživatele. Hesla i client secrets čte jen ze serverové `.env`.

## Paperless OIDC

Compose nastavuje `PAPERLESS_APPS=allauth.socialaccount.providers.openid_connect` a strukturovaný `PAPERLESS_SOCIALACCOUNT_PROVIDERS` s discovery URL Keycloak, scopes `openid profile email groups` a `client_secret_basic`. Automatický signup a synchronizace skupin jsou povolené; stejnojmenné skupiny předem vytvoří `paperless-bootstrap`.

Běžné Paperless přihlášení i lokální admin účet zůstávají během prvního smoke testu zapnuté. `PAPERLESS_DISABLE_REGULAR_LOGIN` ani automatický redirect na SSO nezapínejte, dokud není prakticky ověřeno přihlášení queue managera, všech approverů, skupiny, oprávnění, logout a nouzový admin přístup.

Testovací realm používá HTTP pouze v izolované důvěryhodné síti a `sslRequired=none`. Produkční prostředí musí použít TLS a bezpečné hostname a nesmí převzít testovací secrets.
