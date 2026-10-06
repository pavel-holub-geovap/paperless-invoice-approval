export function PdfPreview({ url, title = "Originální faktura", missing = false }: { url: string; title?: string; missing?: boolean }) {
  return missing ? <div className="source-missing">Originální PDF již není v Paperless dostupné.</div> : <>
    <iframe title={title} src={url}/>
    <a className="button secondary" href={url} target="_blank" rel="noreferrer">Otevřít {title === "Originální faktura" ? "originální" : "schválené"} PDF v novém okně</a>
  </>;
}
