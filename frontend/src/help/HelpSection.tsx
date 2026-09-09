import type { ReactNode } from "react";

type Props = {
  id: string;
  title: string;
  children: ReactNode;
};

export function HelpSection({ id, title, children }: Props) {
  const titleId = `${id}-title`;
  return <section className="help-section" id={id} aria-labelledby={titleId}>
    <h2 id={titleId}>{title}</h2>
    {children}
    <a className="help-back-link" href="#obsah">↑ Zpět na obsah</a>
  </section>;
}

export function HelpCallout({ kind = "info", children }: { kind?: "info" | "warning" | "important"; children: ReactNode }) {
  return <aside className={`help-callout help-callout-${kind}`}>{children}</aside>;
}
