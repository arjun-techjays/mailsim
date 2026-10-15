import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { RunConnection } from '../api/types';
import { useApi } from '../auth/session';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { CopyButton } from '../components/CopyButton';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { connectionKey, keyPrefix, maskKey, useAgentKeys } from './agentKeys';

type Pending = { kind: 'mint' } | { kind: 'revoke'; keyIds: string[] } | null;

/**
 * The run's agent key: the one this tab holds (masked, reveal + copy), or a way to get one
 * (paste a saved key, or mint a new one), plus the run's key ids with revocation.
 */
export function KeyPanel({ conn, revealed, onReveal }: { conn: RunConnection; revealed: boolean; onReveal: (v: boolean) => void }) {
  const api = useApi();
  const qc = useQueryClient();
  const keys = useAgentKeys();
  const held = keys.get(conn.run_id);
  const [pending, setPending] = useState<Pending>(null);
  const [paste, setPaste] = useState('');
  const [pasteError, setPasteError] = useState('');
  const [minted, setMinted] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const active = conn.agent_keys.filter((k) => !k.revoked);
  /** The listed key this tab holds (matched by its public prefix). */
  const heldId = held?.prefix ? conn.agent_keys.find((k) => k.prefix === held.prefix)?.key_id : undefined;
  const mintedNotice = useRef<HTMLParagraphElement>(null);
  const keysHeading = useRef<HTMLHeadingElement>(null);
  const [focusAfter, setFocusAfter] = useState<'minted' | 'keys' | null>(null);
  useEffect(() => {
    if (focusAfter === 'minted') mintedNotice.current?.focus();
    if (focusAfter === 'keys') keysHeading.current?.focus();
    if (focusAfter) setFocusAfter(null);
  }, [focusAfter]);

  const refresh = () => qc.invalidateQueries({ queryKey: connectionKey(conn.run_id) });
  const close = () => {
    setPending(null);
    opener.current?.focus();
  };
  const ask = (p: Pending, el: HTMLElement) => {
    opener.current = el;
    setPending(p);
  };

  const mint = useMutation({
    mutationFn: () => api.mintAgentKey(conn.run_id),
    gcTime: 0,
    onSuccess: (cred) => {
      keys.set(conn.run_id, {
        apiKey: cred.api_key,
        prefix: keyPrefix(cred.api_key),
        mailboxAddress: cred.mailbox_address,
        mailboxId: cred.mailbox_id,
      });
      setMinted(true);
      setFocusAfter('minted');
      onReveal(true);
      setPending(null);
      mint.reset(); // the secret lives in the key store only
      void refresh();
    },
  });
  const revoke = useMutation({
    mutationFn: async (ids: string[]) => {
      for (const id of ids) await api.revokeKey(id);
    },
    onSuccess: (_v, ids) => {
      // Forget the held key when IT was revoked, or when no active key is left (then the held
      // key, whatever it was, cannot work any more).
      if ((heldId && ids.includes(heldId)) || active.every((k) => ids.includes(k.key_id))) keys.forget(conn.run_id);
      setPending(null);
      setFocusAfter('keys'); // the Revoke button is gone: continue from the key list
    },
    onSettled: () => void refresh(),
  });

  const submitPasted = (e: FormEvent) => {
    e.preventDefault();
    const v = paste.trim();
    if (!v) {
      setPasteError('Paste the agent key first.');
      return;
    }
    keys.set(conn.run_id, { apiKey: v, prefix: keyPrefix(v), mailboxAddress: conn.mailbox_address, mailboxId: conn.mailbox_id });
    setPaste('');
    setPasteError('');
    setMinted(false);
  };

  return (
    <section aria-labelledby="key-h" className="card" id="agent-key" data-testid="key-panel">
      <h2 id="key-h">Agent key</h2>
      {held ? (
        <>
          {minted && (
            <p className="notice" ref={mintedNotice} tabIndex={-1} data-testid="minted-notice">
              New agent key minted. Copy it now: the API never shows it again. The run’s earlier agent keys were revoked.
            </p>
          )}
          <p>
            Key for <strong>{held.mailboxAddress}</strong>. It is held in this tab’s memory only: reloading the page
            forgets it.
          </p>
          <div className="row">
            <code className="key-value" data-testid="agent-key-value">
              {revealed ? held.apiKey : maskKey(held.apiKey)}
            </code>
            <button type="button" aria-pressed={revealed} onClick={() => onReveal(!revealed)}>
              {revealed ? 'Hide key' : 'Reveal key'}
            </button>
            <CopyButton value={held.apiKey} label="Copy key" />
            <button
              type="button"
              onClick={() => {
                keys.forget(conn.run_id);
                onReveal(false);
                setMinted(false);
              }}
            >
              Forget key
            </button>
          </div>
          <p className="hint">Snippets below show the real key only while it is revealed.</p>
        </>
      ) : (
        <>
          <p data-testid="no-key">
            Agent keys are shown once: when the run is created and when a new key is minted. This tab does not hold a
            key for this run. Paste the key you saved, or mint a new one.
          </p>
          <form className="form inline" onSubmit={submitPasted} noValidate aria-label="Paste a key you saved">
            <div className="field">
              <label htmlFor="paste-key">Saved agent key</label>
              <input
                id="paste-key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={paste}
                onChange={(e) => setPaste(e.target.value)}
                aria-invalid={!!pasteError}
                aria-describedby={pasteError ? 'paste-key-err' : undefined}
              />
              {pasteError && (
                <p id="paste-key-err" className="field-error">
                  {pasteError}
                </p>
              )}
            </div>
            <button type="submit">Use this key</button>
          </form>
        </>
      )}
      <div className="row">
        <button type="button" className={held ? undefined : 'primary'} onClick={(e) => ask({ kind: 'mint' }, e.currentTarget)}>
          Mint a new agent key
        </button>
      </div>
      {pending?.kind === 'mint' && (
        <ConfirmDialog title="Mint a new agent key?" confirmLabel="Mint key" busy={mint.isPending} onConfirm={() => mint.mutate()} onCancel={close}>
          <p>
            The server issues a new key for {conn.mailbox_address} and revokes this run’s other agent keys
            {active.length > 0 && (
              <>
                {' '}
                ({active.map((k) => k.key_id).join(', ')})
              </>
            )}
            . An agent still using an old key gets 401 from then on.
          </p>
        </ConfirmDialog>
      )}
      {mint.isError && <ErrorDisplay error={mint.error} title="Could not mint an agent key" />}

      <h3 ref={keysHeading} tabIndex={-1}>
        This run’s agent keys
      </h3>
      {conn.agent_keys.length === 0 ? (
        <p className="empty">No agent keys have been issued for this run.</p>
      ) : (
        <table data-testid="agent-keys">
          <caption>Key ids only: the API never returns a key’s secret again.</caption>
          <thead>
            <tr>
              <th scope="col">Key id</th>
              <th scope="col">Created</th>
              <th scope="col">Expires</th>
              <th scope="col">Status</th>
              <th scope="col">Action</th>
            </tr>
          </thead>
          <tbody>
            {conn.agent_keys.map((k) => (
              <tr key={k.key_id}>
                <th scope="row">
                  <code>{k.key_id}</code>
                </th>
                <td>{k.created_at}</td>
                <td>{k.expires_at ?? 'with the run'}</td>
                <td>
                  {k.revoked ? 'revoked' : 'active'}
                  {k.key_id === heldId && <strong data-testid="held-marker"> · held in this tab</strong>}
                </td>
                <td>
                  {!k.revoked && (
                    <button type="button" onClick={(e) => ask({ kind: 'revoke', keyIds: [k.key_id] }, e.currentTarget)}>
                      Revoke <span className="visually-hidden">{k.key_id}</span>
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {active.length > 1 && (
        <button type="button" onClick={(e) => ask({ kind: 'revoke', keyIds: active.map((k) => k.key_id) }, e.currentTarget)}>
          Revoke all agent keys of this run
        </button>
      )}
      {pending?.kind === 'revoke' && (
        <ConfirmDialog
          title={pending.keyIds.length === 1 ? `Revoke ${pending.keyIds[0]}?` : `Revoke ${pending.keyIds.length} agent keys?`}
          confirmLabel="Revoke"
          busy={revoke.isPending}
          onConfirm={() => revoke.mutate(pending.keyIds)}
          onCancel={close}
        >
          <p>An agent using {pending.keyIds.length === 1 ? 'this key' : 'these keys'} is cut off at once (401 on every interface). This cannot be undone.</p>
        </ConfirmDialog>
      )}
      {revoke.isError && <ErrorDisplay error={revoke.error} title="Could not revoke the key" />}
    </section>
  );
}
