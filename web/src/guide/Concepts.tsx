import { Link } from 'react-router-dom';
import { CONCEPTS, concept, type ConceptId } from './conceptList';

/** Small inline "What is this?" disclosure for one concept. */
export function WhatIsThis({ term, label }: { term: ConceptId; label?: string }) {
  const c = concept(term);
  return (
    <details className="what-is" data-testid={`what-is-${term}`}>
      <summary>{label ?? c.question}</summary>
      <p>{c.short}</p>
      <p>{c.more}</p>
      <p>
        <Link to={`/concepts#${c.id}`}>All concepts</Link>
      </p>
    </details>
  );
}

/** The full glossary (Concepts page). */
export function Glossary() {
  return (
    <dl className="glossary" data-testid="glossary">
      {CONCEPTS.map((c) => (
        <div key={c.id} id={c.id} className="glossary-item">
          <dt>{c.term}</dt>
          <dd>
            <p>{c.short}</p>
            <p className="muted">{c.more}</p>
          </dd>
        </div>
      ))}
    </dl>
  );
}
