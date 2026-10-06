import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

export function PdfPreview({ url, title = "Originální faktura", missing = false }: { url: string; title?: string; missing?: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [error, setError] = useState(false);
  const [rendered, setRendered] = useState(false);
  useEffect(() => {
    if (missing) return;
    let disposed = false;
    let loading: { destroy: () => Promise<void> } | undefined;
    setDocument(null); setPageNumber(1); setError(false); setRendered(false);
    void import("pdfjs-dist").then(async pdfjs => {
      if (disposed) return;
      pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
      const task = pdfjs.getDocument({url, withCredentials: true});
      loading = task;
      const pdf = await task.promise;
      if (!disposed) setDocument(pdf);
    }).catch(() => { if (!disposed) setError(true); });
    return () => { disposed = true; void loading?.destroy(); };
  }, [url, missing]);
  useEffect(() => {
    if (!document) return;
    let disposed = false;
    let task: RenderTask | undefined;
    setRendered(false);
    void document.getPage(pageNumber).then(async page => {
      if (disposed || !canvas.current) return;
      const viewport = page.getViewport({scale: 1.5});
      canvas.current.width = Math.ceil(viewport.width);
      canvas.current.height = Math.ceil(viewport.height);
      task = page.render({canvas: canvas.current, viewport});
      await task.promise;
      if (!disposed) setRendered(true);
    }).catch(() => { if (!disposed) setError(true); });
    return () => { disposed = true; task?.cancel(); };
  }, [document, pageNumber]);
  if (missing) return <div className="source-missing">Originální PDF již není v Paperless dostupné.</div>;
  return <>
    <div className="pdf-preview" aria-label={title}>
      {!error && <>
        <div className="pdf-toolbar">
          <button className="button secondary" disabled={!document || pageNumber <= 1 || !rendered} onClick={() => setPageNumber(pageNumber - 1)}>Předchozí strana</button>
          <span aria-live="polite">{document ? `Strana ${pageNumber} / ${document.numPages}` : "Načítám PDF…"}</span>
          <button className="button secondary" disabled={!document || pageNumber >= document.numPages || !rendered} onClick={() => setPageNumber(pageNumber + 1)}>Další strana</button>
        </div>
        <canvas ref={canvas} role="img" aria-label={`${title}, strana ${pageNumber}`} hidden={!rendered}/>
        {!rendered && <p role="status">Vykresluji náhled PDF…</p>}
      </>}
      <iframe title={title} src={url} hidden={!error}/>
      {error && <p role="status">Náhled nelze vykreslit. Použijte samostatný PDF odkaz.</p>}
    </div>
    <a className="button secondary" href={url} target="_blank" rel="noreferrer">Otevřít {title === "Originální faktura" ? "originální" : "schválené"} PDF v novém okně</a>
  </>;
}
