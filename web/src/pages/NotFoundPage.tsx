import { Link } from 'react-router-dom';
import { PageIntro } from '../components/PageIntro';
import { usePageTitle } from '../hooks/usePageTitle';

export function NotFoundPage() {
  usePageTitle('Page not found');
  return (
    <>
      <h1>Page not found</h1>
      <PageIntro
        next={[
          { to: '/scenarios', label: 'Scenarios' },
          { to: '/runs', label: 'Runs' },
          { to: '/concepts', label: 'Concepts' },
        ]}
      >
        <p>
          There is nothing at this address. It may be a link to a page this version of the dashboard does not have.
        </p>
      </PageIntro>
      <p>
        <Link to="/">Go to the start page</Link>
      </p>
    </>
  );
}
