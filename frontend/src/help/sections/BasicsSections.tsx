import { HelpCallout, HelpSection } from "../HelpSection";
import { LinearWorkflowDiagram, MainInvoiceWorkflowDiagram } from "../WorkflowDiagrams";

export function BasicsSections() {
  return <>
    <HelpSection id="ucel" title="1. K čemu systém slouží">
      <p>Approval pomáhá přijmout účetní doklad, přečíst jeho obsah, zkontrolovat údaje, rozdělit náklad do interních sekcí a získat dohledatelná schválení. Originální soubor je uložen v Paperless a v Approval vidíte jeho pracovní stav.</p>
      <p>Systém není účetní program. Nevytváří finální předkontace ani účetní rozúčtování a sám nic nezapisuje do POHODY. Po dokončení připraví vhodný podklad pro ruční import nebo další ruční zpracování.</p>
      <HelpCallout><strong>Základní pravidlo:</strong> schvaluje se náklad. Typ dokladu a způsob jeho zpracování jsou samostatné údaje.</HelpCallout>
    </HelpSection>

    <HelpSection id="prihlaseni" title="2. Přihlášení">
      <p>Na přihlašovací obrazovce zvolte <strong>Přihlásit přes Keycloak</strong> a použijte svůj firemní účet. Po přihlášení uvidíte nabídku odpovídající svým rolím.</p>
      <ul>
        <li>Správce fronty začíná ve <strong>Frontě</strong>.</li>
        <li>Schvalovatel začíná v části <strong>Moje schválení</strong>.</li>
        <li><strong>Nápověda</strong> je dostupná oběma rolím.</li>
      </ul>
    </HelpSection>

    <HelpSection id="role" title="3. Role uživatelů">
      <div className="help-role-grid">
        <article className="help-role-card">
          <h3>Schvalovatel</h3>
          <ul>
            <li>vidí přidělené části v záložce Ke schválení,</li>
            <li>může schválit, vrátit nebo zamítnout svůj aktuální úkol,</li>
            <li>vidí svou historii a originály dokladů, ke kterým měl schvalovací vztah,</li>
            <li>může nahrát vlastní PDF a před předáním je připravit pouze ve svých povolených sekcích.</li>
          </ul>
        </article>
        <article className="help-role-card">
          <h3>Správce fronty</h3>
          <ul>
            <li>vidí celou frontu a kontroluje originály i údaje,</li>
            <li>nastavuje typ dokladu, režim, sekce a schvalovatele,</li>
            <li>spravuje oprávnění schvalovatelů k sekcím,</li>
            <li>předává doklady ke schválení, připravuje exporty a potvrzuje skutečně provedený ruční import.</li>
          </ul>
        </article>
      </div>
      <HelpCallout kind="important"><strong>Oprávnění vždy kontroluje server.</strong> Skrytí tlačítka v obrazovce není jedinou ochranou.</HelpCallout>
    </HelpSection>

    <HelpSection id="prubeh" title="4. Jak doklad projde systémem">
      <p>Tok se může mírně lišit podle typu dokladu a podle toho, kdo jej nahrál. Běžná přijatá faktura pro schválení prochází následujícími kroky:</p>
      <MainInvoiceWorkflowDiagram/>
    </HelpSection>

    <HelpSection id="schvalovatel" title="5. Schvalovatel">
      <p>V <strong>Moje schválení</strong> jsou tři záložky: aktuální úkoly, Moje historie a Moje nahrané. Každý úkol se vztahuje ke konkrétní sekci, částce a revizi. Před rozhodnutím otevřete originální PDF a porovnejte je s údaji v úkolu.</p>
      <p>U vlastního nahraného dokladu můžete před předáním upravit fakturační údaje, klasifikaci a rozdělení do sekcí. Vybrat lze pouze sekce, ke kterým máte aktivní oprávnění; systém vám k nim vytvoří vlastní schvalovací úkol.</p>
      <LinearWorkflowDiagram
        id="approver-upload-workflow"
        title="Doklad nahrává schvalovatel"
        steps={[
          "Schvalovatel přetáhne nebo vybere PDF",
          "Paperless uloží originál a provede OCR",
          "ISDOC nebo AI předvyplní údaje",
          "Schvalovatel zkontroluje údaje a vybere své povolené sekce",
          "Schvalovatel může schválit vlastní sekce",
          "Schvalovatel zvolí Předat správci fronty",
          "Správce zkontroluje originál, údaje, klasifikaci, sekce a schvalovatele",
          "Případná významná změna vytvoří novou revizi a vyžádá nová schválení",
          "Po všech schváleních následuje finalizace",
        ]}
        alternative="Schvalovatel nahraje PDF, zkontroluje předvyplněné údaje, rozdělí částku jen do svých povolených sekcí a může tyto vlastní části předběžně schválit. Doklad potom povinně předá správci fronty. Správce provede kontrolu a jeho významné změny mohou založit novou revizi s novým schvalováním."
      />
      <HelpCallout kind="warning"><strong>Vlastní schválení není finální schválení dokladu.</strong> Doklad nahraný schvalovatelem musí vždy zkontrolovat a do řádného schvalování předat správce fronty. Před touto kontrolou lze vlastní sekci pouze schválit; vrácení a zamítnutí je dostupné až v běžném schvalování.</HelpCallout>
    </HelpSection>

    <HelpSection id="spravce-fronty" title="6. Správce fronty">
      <p>Správce zpracovává doklady ve Frontě. U každého dokladu vidí vlevo originální PDF a vpravo klasifikaci, zdroj vytěžení, fakturační údaje, validace, sekce, schvalovatele, workflow a audit.</p>
      <LinearWorkflowDiagram
        id="queue-manager-workflow"
        title="Doklad nahrává správce fronty"
        steps={[
          "Správce fronty nahraje dokument",
          "Paperless uloží originál, provede OCR a Approval zvolí ISDOC nebo AI",
          "Správce zkontroluje originál, údaje a klasifikaci",
          "Správce rozdělí částku do sekcí a přiřadí oprávněné schvalovatele",
          "Správce potvrdí kontrolu originálu a předá doklad ke schválení",
          "Schvalovatelé rozhodnou o všech povinných částech",
          "Systém dokončí schválení a vytvoří schválenou kopii",
          "Správce připraví podklad pro POHODU podle typu dokladu",
        ]}
        alternative="Správce nahraje dokument, zkontroluje originál a vytěžené údaje, nastaví klasifikaci, sekce a oprávněné schvalovatele. Po potvrzení originálu jej předá ke schválení. Po všech povinných souhlasech vznikne schválená kopie a případný podklad pro ruční import do POHODY."
      />
    </HelpSection>

    <HelpSection id="nahrani" title="7. Nahrání nového dokladu">
      <p>PDF můžete přetáhnout do plochy <strong>Přetáhněte fakturu sem</strong> nebo použít tlačítko <strong>+ Nahrát fakturu</strong> či <strong>+ Nahrát doklad</strong>. Je možné vybrat více PDF; každý soubor se zpracuje samostatně.</p>
      <ol>
        <li>Approval bezpečně předá PDF do Paperless. Originální soubor se trvale neduplikuje do business databáze Approval.</li>
        <li>Paperless dokument uloží a provede OCR. Approval potom zkontroluje případný vložený ISDOC nebo spustí AI vytěžení.</li>
        <li>Průběžný stav vidíte pod nahrávací plochou. Po dokončení se doklad objeví ve Frontě nebo v záložce Moje nahrané.</li>
      </ol>
      <p>OCR a lokální AI mohou podle délky dokumentu chvíli trvat. Obrazovka se sama pravidelně obnovuje; není nutné nahrávat soubor znovu.</p>
      <HelpCallout><strong>Rozdíl rolí:</strong> správce pokračuje přímo úplnou kontrolou ve Frontě. Schvalovatel připravuje jen vlastní nahraný doklad, používá své povolené sekce a nakonec jej musí předat správci fronty.</HelpCallout>
    </HelpSection>
  </>;
}
