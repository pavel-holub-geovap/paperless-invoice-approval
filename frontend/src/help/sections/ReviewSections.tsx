import { HelpCallout, HelpSection } from "../HelpSection";
import { LinearWorkflowDiagram } from "../WorkflowDiagrams";

export function ReviewSections() {
  return <>
    <HelpSection id="fakturacni-udaje" title="8. Kontrola fakturačních údajů">
      <p>Systém předvyplní například dodavatele, číslo faktury, data, platební údaje, základ, DPH a celkovou částku. Zdroj dat vidíte v detailu: platný ISDOC, OCR / AI, nebo ruční zadání.</p>
      <ol>
        <li>Porovnejte každou důležitou hodnotu s originálním PDF vlevo.</li>
        <li>Chybnou hodnotu opravte ve Fakturačních údajích.</li>
        <li>Zvolte <strong>Uložit změny</strong>. Systém vytvoří novou revizi, je-li změna významná, a znovu provede deterministické kontroly nad aktuálními hodnotami.</li>
      </ol>
      <HelpCallout kind="important"><strong>AI není autorita.</strong> Rozhodující jsou ověřené údaje na originálu a jejich aktuální verze uložená uživatelem.</HelpCallout>
      <h3>DPH, součty a zaokrouhlení</h3>
      <p>Kontroly porovnávají základ, DPH, celkem a jednotlivé DPH řádky. Hodnoty vytištěné na faktuře zůstávají rozhodující; výpočet pomáhá najít nesrovnalost. Zaokrouhlení může vysvětlit drobný rozdíl a systém je ukáže samostatně.</p>
      <dl className="help-meaning-list">
        <div><dt>OK</dt><dd>Kontrola je v pořádku.</dd></div>
        <div><dt>Upozornění</dt><dd>Systém našel možnou nesrovnalost. Porovnejte ji s originálem; samotné upozornění nemusí zablokovat pokračování.</dd></div>
        <div><dt>Blokující chyba</dt><dd>Doklad nelze předat do dalšího kroku, dokud chybu neopravíte.</dd></div>
      </dl>
    </HelpSection>

    <HelpSection id="sekce" title="9. Sekce a rozdělení částek">
      <p>Jednu fakturu lze rozdělit mezi více interních sekcí. Každý řádek obsahuje sekci, částku nebo procento a může mít poznámku. Součet aktivních částí musí odpovídat celkové částce dokladu.</p>
      <ul>
        <li>Správce fronty může nastavovat sekce a přiřazovat k nim schvalovatele.</li>
        <li>Schvalovatel při přípravě vlastního uploadu vidí pouze sekce, ke kterým má oprávnění, a je k nim přiřazen jako schvalovatel.</li>
        <li>Globální číselník sekcí a oprávnění schvalovatelů spravuje administrátor v části <strong>Administrace</strong>.</li>
        <li>Každá povinná část aktuální revize musí mít oprávněného schvalovatele.</li>
      </ul>
      <HelpCallout kind="warning"><strong>Sekce slouží pro interní schvalování nákladu.</strong> Nejde automaticky o finální účetní středisko, předkontaci ani účet v POHODĚ.</HelpCallout>
    </HelpSection>

    <HelpSection id="schvalovani" title="10. Schvalování">
      <p>Úkol v části <strong>Ke schválení</strong> patří konkrétní revizi, sekci a částce. Otevřete originální PDF, zkontrolujte obsah a potom rozhodněte o své části.</p>
      <ul>
        <li><strong>Schválit</strong> znamená, že souhlasíte s přidělenou částí.</li>
        <li>Jeden úkol nemůže mít současně více platných rozhodnutí.</li>
        <li>Finální schválení vznikne až po kontrole správce fronty a po schválení všech povinných úkolů aktuální revize.</li>
      </ul>
      <HelpCallout><strong>Self-approval není finální approval.</strong> Předběžné schválení vlastní sekce u dokladu nahraného schvalovatelem nenahrazuje kontrolu správce fronty ani ostatní povinná schválení.</HelpCallout>
    </HelpSection>

    <HelpSection id="vraceni-zamitnuti" title="11. Vrácení a zamítnutí">
      <h3>Vrátit</h3>
      <p>Použijte, když je potřeba doklad doplnit nebo opravit. Komentář je povinný a měl by přesně popsat, co je potřeba změnit. Dokument přejde do stavu Vráceno k doplnění.</p>
      <h3>Zamítnout</h3>
      <p>Použijte, když doklad nemá pokračovat ve schvalování. Komentář je povinný. Zamítnutí platí pro celý dokument; správce fronty jej může podle aktuálních pravidel znovu otevřít k posouzení.</p>
      <p>U vlastní sekce schvalované ještě před kontrolou správce fronty je dostupné pouze schválení. Vrácení a zamítnutí se používá až u řádného úkolu po této kontrole.</p>
    </HelpSection>

    <HelpSection id="revize" title="12. Změna a nová revize">
      <p>Revize určuje přesnou podobu údajů, sekcí a schvalovacích úkolů, o které se rozhoduje. Významná změna fakturačních údajů, klasifikace, sekcí nebo schvalovatelů po rozběhnutí procesu založí novou revizi.</p>
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
