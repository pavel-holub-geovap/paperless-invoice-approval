# Údržba integrované uživatelské dokumentace

Uživatelská příručka je součástí Approval frontendu a po přihlášení je dostupná
na route `/help`. Není to kopie vývojářských dokumentů: popisuje ovládání a
aktuální business chování běžnému schvalovateli a správci fronty.

## Struktura

- `frontend/src/help/HelpPage.tsx` skládá stránku a drží jediný seznam kapitol
  pro obsah.
- `frontend/src/help/sections/` dělí obsah do tematických komponent, aby změna
  jedné části nevyžadovala úpravu jednoho obrovského souboru.
- `frontend/src/help/WorkflowDiagrams.tsx` obsahuje znovupoužitelné HTML diagramy
  a jejich textové alternativy.
- `frontend/src/help/help.css` řídí vzhled, responzivitu, focus a tisk.
- `frontend/src/help/HelpPage.test.tsx` ověřuje role, route, obsah, anchors,
  diagramy a klíčové business hranice.

Nápovědu vykresluje stejná React/Vite aplikace. Produkční frontendový Nginx už
vrací `index.html` pro History API deep links, takže není potřeba samostatný
server ani route backendu. Dokumentace neobsahuje externí CDN nebo runtime
requests a po načtení aplikace funguje bez přístupu k internetu.

## Pravidla změn

Při změně workflow, rolí, klasifikace nebo uživatelských labelů porovnejte
příručku alespoň s:

1. `backend/app/models.py` a `backend/app/services/workflow.py`,
2. frontendovým UI a `frontend/src/lib/labels.ts`,
3. `docs/ARCHITECTURE.md`, `docs/CURRENT_STATE.md` a `docs/DECISIONS.md`,
4. business a frontendovými testy.

Interní kódy enumů se v běžném textu nezobrazují. Termín `Sekce` je uživatelský
název pro interní schvalovací rozdělení; nesmí být prezentován jako automatické
účetní středisko POHODA. U POHODY musí vždy zůstat výslovně uveden ruční import.
AI je pouze návrh a platný ISDOC má přednost.

Nová kapitola musí mít stabilní ASCII `id`, položku v `helpChapters`, správnou
úroveň nadpisu a cílený test. Diagram musí mít vedle vizuální HTML/CSS podoby také
textovou alternativu. Nepřidávejte screenshoty s reálnými doklady, hesla, tokeny,
interní credentials nebo odkazy s citlivými parametry.

## Ověření

Po obsahové změně spusťte celou frontendovou testovací sadu, TypeScript kontrolu
a produkční Vite build. V browseru ověřte minimálně:

- přístup přes hlavní menu jako `QUEUE_MANAGER` i `APPROVER`,
- přímou route a odkazy `/help/#schvalovani`, `/help/#sekce` a `/help/#pohoda`,
- čitelnost diagramů bez horizontálního overflow na desktopu i úzkém viewportu,
- klávesnicový focus a viditelné textové alternativy,
- print preview, ve kterém je skryta aplikační hlavička a zůstává obsah i diagramy.
