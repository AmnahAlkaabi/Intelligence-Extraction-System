import { useState, type ReactNode } from "react";

export function CollapsibleSection({
  title, defaultOpen, badge, children,
}: { title: string; defaultOpen: boolean; badge?: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="section-panel">
      <button className="section-header" onClick={() => setOpen((o) => !o)}>
        <span className={`section-chevron ${open ? "is-open" : ""}`}>▸</span>
        <span className="section-title">{title}</span>
        {badge}
      </button>
      {open && <div className="section-body">{children}</div>}
    </section>
  );
}
