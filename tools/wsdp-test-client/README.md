# WSDP 3.1 – testovací klient Windows Forms

Samostatná aplikace v C# pro zadání uživatelského jména a hesla a ověření volání
webových služeb dálkového přístupu ČÚZK. Je oddělena od systému Approval.

## Spuštění

Vyžaduje Windows s **.NET Framework 4.8 nebo novějším**. Nepotřebuje NuGet balíčky,
.NET SDK ani server. Pokud máte balíček `WsdpTestClient-win.zip`, rozbalte ho a
spusťte **WsdpTestClient.exe**. Konfigurační soubor ponechte vedle EXE.

Pokud máte zdrojové soubory, v této složce spusťte:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -Run
```

Příkaz aplikaci sestaví pomocí C# kompilátoru dodávaného s .NET Frameworkem.
Poté ji lze spouštět přímo jako `bin\Release\WsdpTestClient.exe`. Při přenosu na
jiný počítač přibalte také `WsdpTestClient.exe.config`.

Pro úpravy ve Visual Studiu otevřete `WsdpTestClient.csproj`; pro tento způsob
sestavení potřebujete workload **.NET desktop development** a targeting pack
.NET Framework 4.8. Formulář je sestaven programově v `MainForm.cs`.

## Použití

1. Ponechte prostředí **Na zkoušku**, nebo zvolte **Produkce** pro vlastní účet WSDP.
2. Zadejte jméno a heslo. Tlačítko **Vyplnit veřejný testovací účet** v prostředí na
   zkoušku vyplní veřejný účet `WSTEST` / `WSHESLO` zveřejněný ČÚZK.
3. Ponechte operaci **Ověřit přihlášení (stavWS)** a klikněte na **Zavolat WSDP**.
4. V souhrnu se objeví HTTP stav, zprávy WSDP, přihlášený uživatel a verze služby,
   pokud je služba vrátí. V záložkách je odpověď XML a náhled SOAP požadavku se
   skrytým heslem. Odpověď lze explicitně uložit tlačítkem **Uložit odpověď**.

| Operace | Služba | Účel |
| --- | --- | --- |
| `stavWS` | `ucet` | Ověření přihlášení a dostupnosti služby |
| `seznamKraju` | `ciselnik` | Čtecí volání vracející seznam krajů |
| `seznamCiselniku` | `ciselnik` | Čtecí volání vracející přehled číselníků |

Tyto tři operace nemají vstupní parametry. Aplikace negeneruje ani nestahuje
placené výstupní sestavy. Testovací prostředí obsahuje fiktivní data; produkce
vyžaduje účet s oprávněním WSDP. Při změně prostředí se přihlašovací údaje vymažou.

Přihlašování používá **SOAP 1.1 / WS-Security UsernameToken PasswordText přes HTTPS**,
nikoli HTTP Basic. Každý požadavek má nový kryptografický nonce a UTC čas vytvoření.
Heslo se odesílá jen na přednastavený endpoint ČÚZK zvoleného prostředí. Klient
nenásleduje HTTP přesměrování a nevypíná ověřování TLS certifikátů.

Heslo zůstává pouze v paměti aplikace a neukládá se do konfigurace ani logu.
Náhled požadavku je ilustrativní: má vlastní nonce/čas a neobsahuje skutečné heslo.
V zobrazené a uložené odpovědi jsou případná odražená hesla maskována. Obsah odpovědi
ale může obsahovat údaje účtu, proto ukládejte jen tam, kde jej chcete uchovat.

Časový limit lze nastavit na 5–300 sekund. Volání je asynchronní a lze je zrušit.
Chyby HTTP, SOAP Fault a aplikační chyby `CHYBA` uvnitř HTTP 200 jsou rozlišeny;
HTTP 200 samo o sobě neznamená úspěch. Při `FailedAuthentication` ověřte účet,
prostředí, oprávnění WSDP a případné omezení IP. Při chybě nonce/času zkontrolujte
synchronizaci hodin Windows. XML parser zakazuje DTD a externí entity;
odpověď je omezena na 2 MiB.

## Sestavení a testy

```powershell
# Sestavení + lokální testy bez přístupu do sítě:
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -Test

# Navíc skutečná čtecí volání na bezplatném trialu a odmítnutí neexistujícího účtu:
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -Live

# Přenositelný ZIP s EXE, konfigurací a návodem:
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -Package
```

Lokální testy pokrývají SOAP obálku, escapování XML, WS-Security, nonce, endpointy,
SOAPAction, UTF-8, HTTP chyby, SOAP Fault, aplikační chybu v HTTP 200, namespace
zděděné ze společného XSD, maskování hesla, zákaz DTD, limit odpovědi, zrušení,
timeout a základní ovládání formuláře. Vytvoří také `bin\Release\ui-preview.png`.
Síťové testy používají výhradně veřejný trial účet, nikdy produkci.

## Podklady

Implementace vychází z uživatelem dodané
[technické dokumentace WSDP z 10. 4. 2026 (ZIP)](https://www.cuzk.gov.cz/Katastr-nemovitosti/Poskytovani-udaju-z-KN/Dalkovy-pristup/Webove-sluzby-dalkoveho-pristupu/WSDP_10_4_2026.aspx),
konkrétně WSDL/XSD `ws3_1/wsdp/ucet_v3_1` a `ciselnik_v3_1` a společného
`xsd/commonTypes_v3_1.xsd`. Dokumentace obsahuje i starší rozhraní; aplikace
používá verzi **3.1**.

- [Přehled a aktuální dokumentace WSDP](https://cuzk.gov.cz/Katastr-nemovitosti/Poskytovani-udaju-z-KN/Dalkovy-pristup/Webove-sluzby-dalkoveho-pristupu.aspx)
- [Prostředí na zkoušku a veřejné účty](https://cuzk.gov.cz/Katastr-nemovitosti/Poskytovani-udaju-z-KN/Dalkovy-pristup/Webove-sluzby-DP-na-zkousku.aspx)
- Trial: `https://wsdptrial.cuzk.gov.cz/trial/ws/wsdp/3.1/{ucet|ciselnik}`
- Produkce: `https://katastr.cuzk.gov.cz/ws/wsdp/3.1/{ucet|ciselnik}`

Aplikace je desktopový nástroj pro Windows. Nemění Approval runtime,
Docker Compose ani databázi a nenasazuje se na linuxový `ubuntudocker`.
