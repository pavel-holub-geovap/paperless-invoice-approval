import { useEffect, useId, useState, type ReactNode } from "react";

export function CollapsibleCard({ title, summary, defaultOpen = false, attention = false, className = "card", children }: {
  title: string; summary?: ReactNode; defaultOpen?: boolean; attention?: boolean; className?: string; children: ReactNode;
}) {
  const id = useId();
  const [open, setOpen] = useState(defaultOpen || attention);
  useEffect(() => { if (attention) setOpen(true); }, [attention]);
  return <section className={`${className} collapsible-card`}>
    <h2 className="collapse-heading"><button type="button" className="collapse-toggle" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
      <span aria-hidden="true">{open ? "▾" : "▸"}</span><span>{title}</span><span className="collapse-summary">{summary}</span>
    </button></h2>
    <div id={id} hidden={!open} className="collapse-body">{children}</div>
  </section>;
}
