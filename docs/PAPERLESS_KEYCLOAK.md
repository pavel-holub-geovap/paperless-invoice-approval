# Společné přihlášení Paperless a Approval přes Keycloak

Keycloak realm `paperless-invoice` je společný identity provider, ale každá aplikace má samostatný confidential client a samostatný secret.

## Clients a callback URI

| Client | Veřejná URL | Redirect URI |
| --- | --- | --- |
| `approval-app` | `http://172.30.172.167/` | `http://172.30.172.167/api/auth/callback` |
| `paperless` | `http://172.30.172.167:8000/` | `http://172.30.172.167:8000/accounts/oidc/keycloak/login/callback/` |

Provider ID je `keycloak`; trailing slash callbacku Paperless je záměrný. Issuer je `http://172.30.172.167:8081/realms/paperless-invoice`. Reverse proxy zachovává `Host`, `X-Forwarded-Host`, port a protokol.

`keycloak-provision` idempotentně vytvoří realm, oba clients, realm roles a skupiny `QUEUE_MANAGER`/`APPROVER`, group claim mapper a čtyři testovací uživatele. Hesla i client secrets čte jen ze serverové `.env`.

## Paperless OIDC

Compose nastavuje `PAPERLESS_APPS=allauth.socialaccount.providers.openid_connect` a strukturovaný `PAPERLESS_SOCIALACCOUNT_PROVIDERS` s discovery URL Keycloak, scopes `openid profile email groups` a `client_secret_basic`. Automatický signup a synchronizace skupin jsou povolené; stejnojmenné skupiny předem vytvoří `paperless-bootstrap`.

Běžné Paperless přihlášení i lokální admin účet zůstávají během prvního smoke testu zapnuté. `PAPERLESS_DISABLE_REGULAR_LOGIN` ani automatický redirect na SSO nezapínejte, dokud není prakticky ověřeno přihlášení queue managera, všech approverů, skupiny, oprávnění, logout a nouzový admin přístup.

Testovací realm používá HTTP pouze v izolované důvěryhodné síti a `sslRequired=none`. Produkční prostředí musí použít TLS a bezpečné hostname a nesmí převzít testovací secrets.
