import { useState, type ReactNode } from 'react';

interface SectionProps {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}

export default function Section({ title, children, defaultOpen = false }: SectionProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="section">
      <div className="section-header" onClick={() => setOpen(o => !o)}>
        <h2>{title}</h2>
        <span className="toggle">{open ? '\u2212' : '+'}</span>
      </div>
      {open && <div className="section-content">{children}</div>}
    </div>
  );
}
