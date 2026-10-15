import { Link } from 'react-router-dom';

export interface NextStep {
  to: string;
  label: string;
}

/**
 * Every page says what it is for and what to do next (UX story). `children` holds the short
 * explanation; `next` lists the likely next steps as links.
 */
export function PageIntro({ children, next = [] }: { children: React.ReactNode; next?: NextStep[] }) {
  return (
    <div className="page-intro" data-testid="page-intro">
      <div className="page-intro-text">{children}</div>
      {next.length > 0 && (
        <nav aria-label="Next steps" className="page-intro-next">
          <span className="muted">Next:</span>
          <ul>
            {next.map((n) => (
              <li key={n.to + n.label}>
                <Link to={n.to}>{n.label}</Link>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </div>
  );
}
