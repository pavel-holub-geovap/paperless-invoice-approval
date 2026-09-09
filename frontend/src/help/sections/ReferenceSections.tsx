import { HelpCallout, HelpSection } from "../HelpSection";

const statuses = [
  ["Nový", "Doklad byl objeven a čeká na zpracování."],
  ["AI zpracování", "Probíhá vytěžení bez použitelného ISDOC."],
  ["Kontrola údajů", "Údaje a výsledky kontrol jsou připravené ke kontrole."],
  ["Kontrola správcem fronty", "Doklad čeká na kontrolu a předání správcem."],
  ["Vyžaduje kontrolu", "Po chybě, změně nebo znovuotevření je nutný zásah uživatele."],
  ["Připraveno ke schválení", "Byly splněny podmínky pro vytvoření schvalovacích úkolů."],
  ["Čeká na schválení", "Čeká se na všechna povinná rozhodnutí aktuální revize."],
  ["Vráceno k doplnění", "Schvalovatel požaduje opravu nebo doplnění."],
  ["Zamítnuto", "Dokument byl zamítnut s povinným komentářem."],
  ["Schváleno", "Všechna povinná schválení aktuální revize jsou platná."],
  ["XML připraveno", "Byl připraven mezikrok XML podkladu."],
  ["Připraveno k exportu", "Schválený doklad může vytvořit exportní podklad."],
  ["Export vytvořen", "Neměnný podklad pro ruční import je vytvořen."],
  ["Importováno do POHODY", "Oprávněný uživatel potvrdil skutečný ruční import."],
];

export function ReferenceSections() {
  return <>
    <HelpSection id="stavy" title="17. Stavy dokumentu">
      <p>Stav říká, kde se dokument nachází v hlavním workflow. Dostupnost originálu a případné vyřazení jako duplicity jsou samostatné informace a historický workflow nemažou.</p>
      <div className="help-table-wrap">
        <table className="help-table">
          <thead><tr><th scope="col">Stav v aplikaci</th><th scope="col">Co znamená</th></tr></thead>
          <tbody>{statuses.map(([name, meaning]) => <tr key={name}><th scope="row">{name}</th><td>{meaning}</td></tr>)}</tbody>
        </table>
      </div>
    </HelpSection>

    <HelpSection id="situace" title="18. Nejčastější situace">
      <div className="help-situations">
        <article>
          <h3>AI přečetla špatnou částku</h3>
          <ol><li>Porovnejte údaj s originálem.</li><li>Opravte jej ve Fakturačních údajích.</li><li>Zvolte Uložit změny.</li><li>Zkontrolujte nově přepočítané validace.</li></ol>
        </article>
        <article>
          <h3>Faktura patří do více sekcí</h3>
          <ol><li>Přidejte potřebné řádky.</li><li>Vyberte sekce a zadejte částky nebo procenta.</li><li>Ověřte, že součet odpovídá dokladu.</li><li>Správce přiřadí ke každé části oprávněného schvalovatele.</li></ol>
        </article>
        <article>
          <h3>Správce změnil dokument po mém schválení</h3>
          <p>Významná změna vytvoří novou revizi. Původní rozhodnutí zůstane v historii jako případně zneplatněné a aktuální revize se schvaluje znovu.</p>
        </article>
        <article>
          <h3>Zálohová faktura</h3>
          <p>Může projít interním schválením a získat schválenou kopii PDF, ale do interní POHODY se neimportuje.</p>
        </article>
        <article>
          <h3>Doklad jsem v seznamu ještě nenašel</h3>
          <p>Sledujte dočasný stav uploadu. Paperless může ještě provádět OCR a následné AI zpracování může trvat. Seznam se obnovuje automaticky; případně použijte Obnovit.</p>
        </article>
        <article>
          <h3>Doklad se vrátil</h3>
          <p>Přečtěte komentář schvalovatele, opravte údaje nebo rozdělení a znovu jej předejte. Opětovné předání vytvoří aktuální revizi a zachová historii.</p>
        </article>
      </div>
    </HelpSection>

    <HelpSection id="problemy" title="19. Co dělat při problému">
      <dl className="help-troubleshooting">
        <div><dt>Blokující chyba</dt><dd>Otevřete detail chyby, porovnejte pole s originálem, opravte hodnotu a uložte změny. Potom zkontrolujte nový výsledek validací.</dd></div>
        <div><dt>Upozornění</dt><dd>Prověřte označenou hodnotu proti PDF. Pokud hodnoty na originálu souhlasí, upozornění samo nemusí bránit dalšímu kroku.</dd></div>
        <div><dt>Novější revize na serveru</dt><dd>Rozpracovaná lokální data zůstávají zachována. Rozhodněte se podle zobrazené výzvy, zda načíst novou verzi; nepřepisujte změny bez kontroly.</dd></div>
        <div><dt>Originál v Paperless chybí</dt><dd>Operace vyžadující PDF, nové schválení a nový export jsou zablokované. Historie a dříve vytvořené artefakty zůstávají. Kontaktujte správce fronty.</dd></div>
        <div><dt>Nahrávání selhalo</dt><dd>Přečtěte zobrazený důvod. U dočasné chyby použijte Zkusit znovu; při neznámém výsledku soubor neposílejte opakovaně bez kontroly, aby nevznikla duplicita.</dd></div>
        <div><dt>Nemohu použít sekci</dt><dd>Schvalovatel vidí jen své aktivně povolené sekce. Požádejte správce fronty o kontrolu oprávnění.</dd></div>
      </dl>
      <HelpCallout><strong>Když si nejste jistí:</strong> nic nemažte ani nenahrávejte opakovaně. Poznamenejte číslo dokladu, aktuální stav a text chyby a předejte je správci fronty.</HelpCallout>
    </HelpSection>
  </>;
}
