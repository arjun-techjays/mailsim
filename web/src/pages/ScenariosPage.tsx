import { useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useApi } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { ShowFor } from '../components/Gates';
import { PageIntro } from '../components/PageIntro';
import { WhatIsThis } from '../guide/Concepts';
import { Empty, Loading } from '../components/Status';
import { usePageTitle } from '../hooks/usePageTitle';

export function ScenariosPage() {
  usePageTitle('Scenarios');
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const tag = params.get('tag') ?? '';
  const q = params.get('q') ?? '';
  const [draftTag, setDraftTag] = useState(tag);
  const [draftQ, setDraftQ] = useState(q);

  const query = useQuery({
    queryKey: ['scenarios', { tag, q }],
    queryFn: ({ signal }) => api.listScenarios({ tag: tag || undefined, q: q || undefined }, signal),
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const next = new URLSearchParams();
    if (draftTag.trim()) next.set('tag', draftTag.trim());
    if (draftQ.trim()) next.set('q', draftQ.trim());
    setParams(next);
  };
  const filtered = !!(tag || q);

  return (
    <>
      <h1>Scenarios</h1>
      <PageIntro next={[{ to: '/', label: 'Getting started checklist' }]}>
        <p>
          A scenario is a test case for an email agent: the personas involved, what is already in their mailboxes and
          what happens over time. Use <strong>New scenario</strong> to build one: add personas, give each a mailbox (and
          mark the one your agent uses), put emails in them and schedule events. Open a scenario to edit it, start a
          run, write its answer key or compare agent builds.
        </p>
        <WhatIsThis term="scenario" />
      </PageIntro>
      <ShowFor roles={['controller']}>
        <p>
          <Link className="button primary" to="/scenarios/new">
            New scenario
          </Link>{' '}
          <Link className="button" to="/scenarios/import">
            Import scenario pack
          </Link>
        </p>
      </ShowFor>
      <form className="form inline" onSubmit={onSubmit} role="search" aria-label="Filter scenarios">
        <div className="field">
          <label htmlFor="f-tag">Tag</label>
          <input id="f-tag" value={draftTag} onChange={(e) => setDraftTag(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="f-q">Search</label>
          <input id="f-q" type="search" value={draftQ} onChange={(e) => setDraftQ(e.target.value)} />
        </div>
        <button type="submit">Apply filters</button>
      </form>
      {query.isPending ? (
        <Loading label="Loading scenarios…" />
      ) : query.isError ? (
        <ErrorDisplay error={query.error} title="Could not load scenarios" />
      ) : query.data.length === 0 ? (
        <Empty>
          {filtered ? (
            <>
              No scenarios match these filters. <Link to="/scenarios">Clear the filters</Link>
            </>
          ) : (
            <>
              No scenarios have been published in this project yet. <Link to="/scenarios/new">Create one</Link>, or
              publish a YAML file with <code>POST /v1/scenarios</code> (see the README).
            </>
          )}
        </Empty>
      ) : (
        <table>
          <caption>{query.data.length} scenario(s)</caption>
          <thead>
            <tr>
              <th scope="col">Key</th>
              <th scope="col">Latest version</th>
              <th scope="col">Versions</th>
              <th scope="col">Published</th>
            </tr>
          </thead>
          <tbody>
            {query.data.map((s) => (
              <tr key={s.id}>
                <th scope="row">
                  <Link to={`/scenarios/${encodeURIComponent(s.key)}`}>{s.key}</Link>
                </th>
                <td>{s.latest_version ? `v${s.latest_version.version}` : '—'}</td>
                <td>{s.versions.length}</td>
                <td>{s.latest_version?.created_at ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
