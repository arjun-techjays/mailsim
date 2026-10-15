import { PageIntro } from '../components/PageIntro';
import { Glossary } from '../guide/Concepts';
import { usePageTitle } from '../hooks/usePageTitle';

export function ConceptsPage() {
  usePageTitle('Concepts');
  return (
    <>
      <h1>Concepts</h1>
      <PageIntro
        next={[
          { to: '/', label: 'Getting started checklist' },
          { to: '/scenarios', label: 'Scenarios' },
        ]}
      >
        <p>
          The words the dashboard uses, in one place. In short: a <strong>scenario</strong> declares{' '}
          <strong>personas</strong>; each <strong>run</strong> of it gives every persona a <strong>mailbox</strong>; your
          agent gets an <strong>agent key</strong> for one of them and connects through any <strong>interface</strong>;
          a hidden <strong>answer key</strong> grades what it did.
        </p>
      </PageIntro>
      <Glossary />
    </>
  );
}
