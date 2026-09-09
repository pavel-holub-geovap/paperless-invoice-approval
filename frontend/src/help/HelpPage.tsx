import { BasicsSections } from "./sections/BasicsSections";
import { IntegrationSections } from "./sections/IntegrationSections";
import { ReferenceSections } from "./sections/ReferenceSections";
import { ReviewSections } from "./sections/ReviewSections";
import "./help.css";

export const helpChapters = [
  ["ucel", "K čemu systém slouží"],
  ["prihlaseni", "Přihlášení"],
  ["role", "Role uživatelů"],
  ["prubeh", "Jak doklad projde systémem"],
  ["schvalovatel", "Schvalovatel"],
  ["spravce-fronty", "Správce fronty"],
  ["nahrani", "Nahrání nového dokladu"],
  ["fakturacni-udaje", "Kontrola fakturačních údajů"],
  ["sekce", "Sekce a rozdělení částek"],
  ["schvalovani", "Schvalování"],
  ["vraceni-zamitnuti", "Vrácení a zamítnutí"],
  ["revize", "Změna a nová revize"],
  ["isdoc-ocr-ai", "ISDOC, OCR a AI"],
  ["schvalena-kopie", "Schválená kopie PDF"],
  ["pohoda", "POHODA"],
  ["zalohove-faktury", "Zálohové faktury a další typy"],
  ["stavy", "Stavy dokumentu"],
  ["situace", "Nejčastější situace"],
  ["problemy", "Co dělat při problému"],
] as const;

export function HelpPage() {
  return <article className="help-page">
    <section className="help-hero" aria-labelledby="help-title">
      <p className="eyebrow">Uživatelská příručka</p>
      <h1 id="help-title">Nápověda ke schvalování faktur</h1>
      <p>Praktický průvodce od nahrání dokumentu přes kontrolu a schvalování až po ruční předání do POHODY.</p>
      <small>Dokumentace odpovídá aktuální verzi aplikace.</small>
    </section>

    <div className="help-layout">
      <aside className="help-toc" id="obsah" aria-labelledby="toc-title">
        <h2 id="toc-title">Obsah</h2>
        <nav aria-label="Kapitoly uživatelské příručky">
          <ol>{helpChapters.map(([id, title], index) => <li key={id}><a href={`#${id}`}><span>{index + 1}.</span>{title}</a></li>)}</ol>
        </nav>
      </aside>
      <div className="help-content">
        <BasicsSections/>
        <ReviewSections/>
        <IntegrationSections/>
        <ReferenceSections/>
      </div>
    </div>
  </article>;
}
