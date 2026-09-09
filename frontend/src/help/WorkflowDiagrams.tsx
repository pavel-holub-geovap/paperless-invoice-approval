type LinearDiagramProps = {
  id: string;
  title: string;
  steps: string[];
  alternative: string;
};

function Alternative({ text }: { text: string }) {
  return <details className="diagram-alternative" open>
    <summary>Textový popis schématu</summary>
    <p>{text}</p>
  </details>;
}

export function LinearWorkflowDiagram({ id, title, steps, alternative }: LinearDiagramProps) {
  return <figure className="workflow-diagram" aria-labelledby={`${id}-caption`} data-testid={id}>
    <figcaption id={`${id}-caption`}>{title}</figcaption>
    <div className="flow-sequence" aria-hidden="true">
      {steps.map((step, index) => <div className="flow-step-wrap" key={step}>
        <div className="flow-node">{step}</div>
        {index < steps.length - 1 && <div className="flow-arrow">↓</div>}
      </div>)}
    </div>
    <Alternative text={alternative}/>
  </figure>;
}

export function MainInvoiceWorkflowDiagram() {
  return <figure className="workflow-diagram" aria-labelledby="main-workflow-caption" data-testid="main-workflow">
    <figcaption id="main-workflow-caption">Hlavní průchod faktury systémem</figcaption>
    <div className="decision-flow" aria-hidden="true">
      <div className="flow-node">Nový doklad</div><div className="flow-arrow">↓</div>
      <div className="flow-node">Nahrání a uložení originálu v Paperless</div><div className="flow-arrow">↓</div>
      <div className="flow-node">Rozpoznání dokumentu</div><div className="flow-arrow">↓</div>
      <div className="flow-node flow-decision">Obsahuje platný ISDOC?</div>
      <div className="flow-branches">
        <div className="flow-branch"><strong>ANO</strong><span className="flow-arrow">↓</span><div className="flow-node">Strukturovaná data ISDOC</div></div>
        <div className="flow-branch"><strong>NE</strong><span className="flow-arrow">↓</span><div className="flow-node">OCR text → návrh údajů pomocí AI</div></div>
      </div>
      <div className="flow-arrow">↓</div><div className="flow-node">Fakturační údaje a deterministické kontroly</div>
      <div className="flow-arrow">↓</div><div className="flow-node">Kontrola originálu a údajů uživatelem</div>
      <div className="flow-arrow">↓</div><div className="flow-node">Rozdělení do sekcí a přiřazení schvalovatelů</div>
      <div className="flow-arrow">↓</div><div className="flow-node">Schvalování všech povinných částí</div>
      <div className="flow-arrow">↓</div><div className="flow-node">Finální schválení a schválená kopie PDF</div>
      <div className="flow-arrow">↓</div><div className="flow-node flow-success">Příprava podkladu pro ruční předání do POHODY podle typu dokladu</div>
    </div>
    <Alternative text="Doklad se uloží jako originál v Paperless. Systém nejprve hledá platný ISDOC; pokud jej nenajde, použije OCR a AI. Uživatel zkontroluje údaje a systém provede pevně dané kontroly. Následuje rozdělení do sekcí, všechna povinná schválení, finální schválení, vytvoření schválené kopie a případná příprava podkladu pro ruční import do POHODY."/>
  </figure>;
}

export function PohodaWorkflowDiagram() {
  return <figure className="workflow-diagram" aria-labelledby="pohoda-workflow-caption" data-testid="pohoda-workflow">
    <figcaption id="pohoda-workflow-caption">Rozhodnutí o podkladu pro POHODU</figcaption>
    <div className="pohoda-flow" aria-hidden="true">
      <div className="pohoda-lane">
        <div className="flow-node">Přijatá faktura</div><div className="flow-arrow">↓</div>
        <div className="flow-node flow-decision">Je ISDOC platný?</div>
        <div className="flow-branches">
          <div className="flow-branch"><strong>ANO</strong><span className="flow-arrow">↓</span><div className="flow-node">Schválené PDF s ISDOC</div></div>
          <div className="flow-branch"><strong>NE</strong><span className="flow-arrow">↓</span><div className="flow-node">Deterministicky generované XML</div></div>
        </div>
        <div className="flow-arrow">↓</div><div className="flow-node flow-success">Ruční import do POHODY</div>
      </div>
      <div className="pohoda-lane">
        <div className="flow-node">Přijatá zálohová faktura</div><div className="flow-arrow">↓</div>
        <div className="flow-node">Schválení</div><div className="flow-arrow">↓</div>
        <div className="flow-node">Schválená kopie PDF</div><div className="flow-arrow">↓</div>
        <div className="flow-node flow-muted">Bez importu do interní POHODY</div>
      </div>
    </div>
    <Alternative text="U běžné přijaté faktury se pro ruční import do POHODY použije schválené PDF s platným ISDOC, nebo deterministicky vytvořené XML, pokud použitelný ISDOC chybí. Přijatá zálohová faktura projde schválením a může mít schválenou PDF kopii, ale do interní POHODY se neimportuje."/>
  </figure>;
}
