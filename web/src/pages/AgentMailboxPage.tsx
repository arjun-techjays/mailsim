import { useMemo, useState, type FormEvent } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useApi } from '../auth/session';
import { RequireRole } from '../components/Gates';
import { usePageTitle } from '../hooks/usePageTitle';
import { MailboxInspector } from '../mailbox/MailboxInspector';
import { apiSource } from '../mailbox/source';

/** Agent keys are bound to one mailbox; the id is part of the credential handed out at run creation. */
export function AgentMailboxPicker() {
  usePageTitle('My mailbox');
  const navigate = useNavigate();
  const [id, setId] = useState('');
  const [error, setError] = useState('');
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!id.trim()) {
      setError('Enter the mailbox id from the agent credential.');
      return;
    }
    navigate(`/mailbox/${encodeURIComponent(id.trim())}`);
  };
  return (
    <RequireRole roles={['agent']} what="The agent mailbox view">
      <h1>My mailbox</h1>
      <form className="form inline" onSubmit={onSubmit} noValidate>
        <div className="field">
          <label htmlFor="mbx-id">Mailbox id</label>
          <input
            id="mbx-id"
            value={id}
            onChange={(e) => setId(e.target.value)}
            aria-invalid={!!error}
            aria-describedby={error ? 'mbx-id-err' : undefined}
          />
          {error && (
            <p id="mbx-id-err" className="field-error">
              {error}
            </p>
          )}
        </div>
        <button type="submit">Open mailbox</button>
      </form>
    </RequireRole>
  );
}

export function AgentMailboxPage() {
  const { mailboxId = '' } = useParams();
  usePageTitle(`Mailbox ${mailboxId}`);
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const source = useMemo(() => apiSource(api, mailboxId), [api, mailboxId]);
  return (
    <RequireRole roles={['agent']} what="The agent mailbox view">
      <h1>
        Mailbox <code>{mailboxId}</code>
      </h1>
      <MailboxInspector
        source={source}
        selectedMessage={params.get('message')}
        onSelectMessage={(id) => setParams({ message: id })}
      />
    </RequireRole>
  );
}
