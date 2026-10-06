import { HelpCallout, HelpSection } from "../HelpSection";
import { LinearWorkflowDiagram } from "../WorkflowDiagrams";

export function ReviewSections() {
  return <>
    <HelpSection id="fakturacni-udaje" title="8. Kontrola fakturačních údajů">
      <p>Systém předvyplní například dodavatele, číslo faktury, data, platební údaje, základ, DPH, celkovou částku a explicitní zaokrouhlení. Zdroj dat vidíte v detailu: platný ISDOC, OCR / AI, nebo ruční zadání.</p>
      <p>Detail má skládací sekce ovladatelné tlačítkem, klávesnicí Enter i mezerníkem. Stav zůstává v hlavičce viditelný i při zavření. Fakturační údaje, DPH, sekce a workflow jsou otevřené; OCR, zdrojová metadata a historie jsou zavřené. Validace se při novém upozornění otevře automaticky. Běžící AI ukazuje indikátor; dokončení vychází ze skutečného stavu na serveru.</p>
      <p>U dodavatele lze použít <strong>Ověřit v ARES</strong>. Porovnejte nalezený název a adresu; registr nikdy sám nepřepisuje údaje. Nedostupnost ARES neblokuje práci. Shoda dodavatelského IČO s vlastní účetní jednotkou je pouze upozornění na možnou záměnu odběratele a dodavatele.</p>
      <ol>
        <li>Porovnejte každou důležitou hodnotu s originálním PDF vlevo.</li>
        <li>Chybnou hodnotu opravte ve Fakturačních údajích.</li>
        <li>Zvolte <strong>Uložit změny</strong>. Systém vytvoří novou revizi, je-li změna významná, a znovu provede deterministické kontroly nad aktuálními hodnotami.</li>
      </ol>
      <HelpCallout kind="important"><strong>AI není autorita.</strong> Rozhodující jsou ověřené údaje na originálu a jejich aktuální verze uložená uživatelem.</HelpCallout>
      <h3>DPH, součty a zaokrouhlení</h3>
      <p>Kontroly porovnávají základ, DPH, celkem a jednotlivé DPH řádky. Pole <strong>Zaokrouhlení</strong> lze ručně opravit; prázdná hodnota znamená „není známo“, zatímco 0,00 je explicitní nulové zaokrouhlení bez varování.</p>
      <h3>Typ dokladu a K zaplacení</h3>
      <p>Typ dokladu a údaj <strong>K zaplacení: Ano/Ne</strong> jsou nezávislé. I přijatá faktura může být již uhrazená a mít K zaplacení = Ne. Schvalovatel i předkladatel je určí při přípravě vlastního uploadu, správce fronty je může při kontrole změnit. Bez jednoznačné volby K zaplacení nelze doklad předat.</p>
      <dl className="help-meaning-list">
        <div><dt>OK</dt><dd>Kontrola je v pořádku.</dd></div>
        <div><dt>Upozornění</dt><dd>Systém našel možnou nesrovnalost. Porovnejte ji s originálem; samotné upozornění nemusí zablokovat pokračování.</dd></div>
        <div><dt>Blokující chyba</dt><dd>Doklad nelze předat do dalšího kroku, dokud chybu neopravíte.</dd></div>
      </dl>
    </HelpSection>

    <HelpSection id="sekce" title="9. Sekce a rozdělení částek">
      <p>Jednu fakturu lze rozdělit mezi více interních sekcí. Každý řádek obsahuje sekci, částku nebo procento a může mít poznámku. Součet aktivních částí musí odpovídat celkové částce dokladu.</p>
      <p>Výchozí <strong>Jedna sekce</strong> vyžaduje jen výběr sekce a použije celou částku jako 100 %. Změna celku přepočítá dosud nepotvrzený návrh v nové auditované revizi. Dříve potvrzené rozúčtování musíte výslovně znovu uložit. Volbou <strong>Rozúčtovat na více sekcí</strong> zachováte první řádek a zpřístupníte částky/procenta. Částky lze zadat jako 1497,38, 1497.38 nebo 1 497,38; nejednoznačný zápis aplikace odmítne.</p>
      <ul>
        <li>Správce fronty může nastavovat sekce a přiřazovat k nim schvalovatele.</li>
        <li>Schvalovatel při přípravě vlastního uploadu vidí všechny aktivní sekce. U každé vidí, zda ji při předání automaticky schválí, nebo bude vyžadovat jiného schvalovatele.</li>
        <li>Předkladatel může navrhnout libovolnou aktivní sekci a poznámku, ale nevytváří assignment ani rozhodnutí. Schvalovatele určí správce fronty.</li>
        <li>Poznámka u každého rozdělení je prostý text a po finálním schválení se přenese do schválené PDF kopie bez automatických hranatých závorek.</li>
        <li>Globální číselník sekcí a oprávnění schvalovatelů spravuje administrátor v části <strong>Administrace</strong>.</li>
        <li>Každá povinná část aktuální revize musí mít oprávněného schvalovatele.</li>
      </ul>
      <HelpCallout kind="warning"><strong>Sekce slouží pro interní schvalování nákladu.</strong> Nejde automaticky o finální účetní středisko, předkontaci ani účet v POHODĚ.</HelpCallout>
    </HelpSection>

    <HelpSection id="schvalovani" title="10. Schvalování">
      <p>Úkol v části <strong>Ke schválení</strong> patří konkrétní revizi, sekci a částce. Otevřete originální PDF, zkontrolujte obsah a potom rozhodněte o své části.</p>
      <p>Tlačítko <strong>Zobrazit originál a kontext dokladu</strong> otevře náhled PDF přímo u úkolu. Vedle něj jsou popis, K zaplacení a všechny sekce jen pro čtení; vaše část je zvýrazněná.</p>
      <ul>
        <li><strong>Schválit</strong> znamená, že souhlasíte s přidělenou částí.</li>
        <li>Jeden úkol nemůže mít současně více platných rozhodnutí.</li>
        <li>Finální schválení vznikne až po kontrole správce fronty a po schválení všech povinných úkolů aktuální revize.</li>
      </ul>
      <HelpCallout><strong>Auto-approval není finální approval.</strong> Při předání vlastního uploadu vznikne standardní rozhodnutí APPROVE jen pro sekce, ke kterým má uploader v tom okamžiku oprávnění. Nenahrazuje kontrolu správce fronty ani ostatní povinná schválení.</HelpCallout>
    </HelpSection>

    <HelpSection id="vraceni-zamitnuti" title="11. Vrácení a zamítnutí">
      <h3>Vrátit</h3>
      <p>Použijte, když je potřeba doklad doplnit nebo opravit. Důvod je doporučený, ale nepovinný. Dokument přejde do stavu Vráceno k doplnění; správce nahoře vidí kdo a kdy jej vrátil, a důvod, pokud byl uveden.</p>
      <h3>Zamítnout</h3>
      <p>Použijte, když doklad nemá pokračovat ve schvalování. Komentář je doporučený, nikoli povinný. Zamítnutí platí pro celý dokument; správce fronty jej může podle aktuálních pravidel znovu otevřít k posouzení.</p>
      <p>Automatické schválení uploaderovy oprávněné části vzniká při předání správci. Vrácení a zamítnutí se používá až u řádného úkolu po kontrole správce fronty.</p>
    </HelpSection>

    <HelpSection id="revize" title="12. Změna a nová revize">
      <p>Revize určuje přesnou podobu údajů, K zaplacení, zaokrouhlení, sekcí, poznámek a schvalovacích úkolů, o které se rozhoduje. Významná změna těchto údajů nebo klasifikace založí novou revizi.</p>
      <p>Schvalovatelé zachovaných sekcí se přenesou pouze při stále platné identitě, roli a oprávnění. Nové úkoly jsou vždy čekající, nikoli schválené; stará rozhodnutí jsou zneplatněna. Při změně sekce nebo ztrátě oprávnění musí správce vybrat nového schvalovatele. Historie workflow ukazuje lidsky čitelné změny; technický systémový audit je pouze v Administraci.</p>
      <LinearWorkflowDiagram
        id="revision-workflow"
        title="Významná změna a nové schválení"
        steps={[
          "Dokument – revize 1",
          "Schválení revize 1",
          "Významná změna údajů, klasifikace, sekcí nebo schvalovatelů",
          "Vznik revize 2",
          "Původní rozhodnutí zůstávají v historii jako zneplatněná",
          "Nová povinná schválení revize 2",
          "Pokračování workflow",
        ]}
        alternative="Dokument je schválen v revizi 1. Významná změna vytvoří revizi 2. Původní schválení se nesmaže, ale zůstane v historii označené jako zneplatněné. Aktuální revizi je nutné znovu schválit."
      />
      <HelpCallout kind="important"><strong>Běžné workflow nemaže historii.</strong> Podstatná změna schvalovaných údajů vyžaduje nové schválení, zatímco starší rozhodnutí zůstávají dohledatelná. Jedinou výjimkou je výslovný nevratný ADMIN PURGE popsaný v kapitole 20.</HelpCallout>
      <h3>Moje historie</h3>
      <p>Schvalovatel zde vidí faktury, ke kterým měl vztah v libovolné revizi. Detail rozlišuje tehdejší rozhodnutí od aktuálního stavu, ukazuje částku, sekci, komentář a případnou pozdější invalidaci. I když originál v Paperless později chybí, historický záznam zůstane zachován.</p>
    </HelpSection>
  </>;
}
