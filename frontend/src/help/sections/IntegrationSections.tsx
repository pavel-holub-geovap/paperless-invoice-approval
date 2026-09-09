import { HelpCallout, HelpSection } from "../HelpSection";
import { PohodaWorkflowDiagram } from "../WorkflowDiagrams";

export function IntegrationSections() {
  return <>
    <HelpSection id="isdoc-ocr-ai" title="13. ISDOC, OCR a AI">
      <h3>ISDOC</h3>
      <p>ISDOC je strukturovaná elektronická informace vložená ve faktuře nebo PDF. Pokud systém najde platný podporovaný ISDOC, použije přímo jeho údaje a standardní AI vytěžení fakturačních polí nespouští.</p>
      <h3>OCR a AI</h3>
      <p>Když použitelný ISDOC chybí, Paperless nejprve pomocí OCR přečte text dokumentu. Lokální AI z textu navrhne strukturované fakturační údaje. Uživatel návrh porovná s originálem a systém nad uloženými údaji provede pevně dané kontroly.</p>
      <div className="help-mini-flow" aria-label="Textový tok OCR a AI">
        <span>PDF</span><b aria-hidden="true">→</b><span>OCR text</span><b aria-hidden="true">→</b><span>návrh AI</span><b aria-hidden="true">→</b><span>kontrola uživatelem</span><b aria-hidden="true">→</b><span>deterministické kontroly</span>
      </div>
      <HelpCallout kind="warning"><strong>AI může udělat chybu.</strong> Proto vždy zkontrolujte originál. AI pouze navrhuje data; nemění workflow, nevybírá sekce ani schvalovatele a nevytváří POHODA XML.</HelpCallout>
    </HelpSection>

    <HelpSection id="schvalena-kopie" title="14. Schválená kopie PDF">
      <p>Po finálním schválení systém vytvoří neměnnou schválenou kopii PDF. V přidané části obsahuje identifikaci workflow a revize, schvalovatele, časy rozhodnutí, sekce a schválené částky.</p>
      <ul>
        <li>Originální PDF v Paperless zůstává beze změny.</li>
        <li>Vložený ISDOC a další přílohy zůstávají zachované.</li>
        <li>Starší schválené kopie se při nové revizi nemažou; zůstanou historické.</li>
      </ul>
      <p>V detailu dokladu lze mezi Originálem a dostupnou Schválenou kopií přepnout.</p>
    </HelpSection>

    <HelpSection id="pohoda" title="15. POHODA">
      <p>Po schválení běžné přijaté faktury připraví správce fronty podklad podle výsledku kontroly ISDOC. Aplikace sama do POHODY nezapisuje a nepřipojuje se k její databázi.</p>
      <PohodaWorkflowDiagram/>
      <ul>
        <li><strong>Platný ISDOC:</strong> pro ruční import se použije schválené PDF se zachovaným ISDOC.</li>
        <li><strong>Bez použitelného ISDOC:</strong> aplikace vytvoří deterministické XML a samostatně ověří jeho formát i cílovou účetní jednotku.</li>
        <li><strong>Potvrzení importu:</strong> stav Importováno do POHODY nastaví oprávněný uživatel teprve po skutečně provedeném ručním importu.</li>
      </ul>
      <HelpCallout kind="important"><strong>Import do POHODY je vždy ruční.</strong> Stav Export vytvořen pouze říká, že existuje podklad; neznamená provedený import.</HelpCallout>
    </HelpSection>

    <HelpSection id="zalohove-faktury" title="16. Zálohové faktury a další typy dokladů">
      <p>Typ dokladu určuje správce fronty nebo jej při přípravě vlastního uploadu předběžně zvolí schvalovatel. Aktuální nabídka obsahuje:</p>
      <ul className="help-columns">
        <li>Nezařazený doklad</li>
        <li>Přijatá faktura</li>
        <li>Přijatá zálohová faktura</li>
        <li>Daňový doklad k přijaté platbě</li>
        <li>Konečné vyúčtování</li>
        <li>Účtenka</li>
        <li>Platba kartou</li>
        <li>Výdaj zaměstnance</li>
        <li>Centrální doklad</li>
        <li>Ostatní podklad</li>
      </ul>
      <p>Samostatně se volí režim <strong>Ke schválení</strong>, <strong>Pouze evidovat</strong> nebo u centrálního dokladu <strong>Centrální ruční zpracování</strong>. Režim Pouze evidovat nevytváří aktivní schvalovací úkoly.</p>
      <HelpCallout kind="warning"><strong>Přijatá zálohová faktura může projít schválením a mít schválenou PDF kopii, ale do interní POHODY se neimportuje.</strong></HelpCallout>
    </HelpSection>
  </>;
}
