import { useQueries, useQuery } from '@tanstack/react-query';
import { Link, Navigate } from 'react-router-dom';
import type { Role } from '../api/types';
import { hasRole, useApi, useSession } from '../auth/session';
import { Probing } from '../components/Gates';
import { PageIntro } from '../components/PageIntro';
import { WhatIsThis } from '../guide/Concepts';
import type { ConceptId } from '../guide/conceptList';
import { deriveProgress, type StepState } from '../guide/progress';
import { usePageTitle } from '../hooks/usePageTitle';

interface Step {
  n: number;
  title: string;
  why: string;
  state: StepState;
  action: { to: string; label: string } | null;
  /** Shown instead of the action when the connected role cannot do this step. */
  roleNote?: string;
  concept?: ConceptId;
}

const STATE_TEXT: Record<StepState, string> = { done: 'Done', todo: 'To do', unknown: 'Not checked' };

const enc = encodeURIComponent;

function Checklist({ role }: { role: Role }) {
  const api = useApi();
  const evaluator = hasRole(role, ['evaluator']);
  const controller = hasRole(role, ['controller']);
  const scenarios = useQuery({ queryKey: ['scenarios', { tag: '', q: '' }], queryFn: ({ signal }) => api.listScenarios({}, signal) });
  const runs = useQuery({ queryKey: ['home', 'runs'], queryFn: () => api.listRuns(null), retry: false });
  const checked = (scenarios.data ?? []).slice(0, 5);
  const answerKeys = useQueries({
    queries: checked.map((s) => ({
      queryKey: ['answer-keys', s.key],
      queryFn: () => api.listAnswerKeys(s.key),
      enabled: evaluator,
      retry: false,
    })),
  });
  const runList = runs.data?.items;
  const latest = runList?.[0];
  const clock = useQuery({
    queryKey: ['home', 'clock', latest?.id],
    queryFn: () => api.events(latest?.id as string, null, 1, { type: 'clock.advanced' }),
    enabled: !!latest,
    retry: false,
  });

  // Any answer key found = done; none found = to do once every checked scenario answered.
  const akFound = answerKeys.reduce((n, q) => n + (q.data?.length ?? 0), 0);
  const akAll = scenarios.isSuccess && answerKeys.every((q) => q.isSuccess);
  const progress = deriveProgress({
    scenarios: scenarios.data,
    answerKeys: !evaluator ? undefined : akFound > 0 ? akFound : akAll ? 0 : undefined,
    runs: runList,
    clockAdvanced: runList === undefined ? undefined : !latest ? false : clock.isSuccess ? clock.data.items.length > 0 : undefined,
  });

  const key = scenarios.data?.[0]?.key;
  const scenarioKey = latest?.scenario_key ?? key;
  const runHref = latest ? `/runs/${enc(latest.id)}` : null;
  const steps: Step[] = [
    {
      n: 1,
      title: 'Create or pick a scenario',
      why: 'A scenario describes the people involved (personas), what is in their mailboxes and what happens over time. Personas get their mailboxes automatically.',
      state: progress.scenario,
      action: controller ? { to: '/scenarios/new', label: 'Create a scenario' } : { to: '/scenarios', label: 'Browse scenarios' },
      concept: 'persona',
    },
    {
      n: 2,
      title: 'Write its answer key',
      why: 'The answer key says what a good agent should do. It stays hidden from the agent and is used only to grade runs.',
      state: progress.answerKey,
      action: evaluator && key ? { to: `/scenarios/${enc(key)}/answer-key`, label: 'Write the answer key' } : null,
      roleNote: evaluator ? 'Create a scenario first.' : 'Answer keys need an evaluator key (or local no-auth mode).',
      concept: 'answer-key',
    },
    {
      n: 3,
      title: 'Start a run',
      why: 'A run is an isolated copy of the scenario with its own mailboxes and clock. Starting one issues the agent key.',
      state: progress.run,
      action: controller && key ? { to: `/scenarios/${enc(key)}/start`, label: 'Start a run' } : null,
      roleNote: controller ? 'Create a scenario first.' : 'Starting runs needs a controller key.',
      concept: 'run',
    },
    {
      n: 4,
      title: 'Connect your agent',
      why: 'Point your agent at the run’s mailbox through the Gmail API (GSuite simulator), Microsoft Graph (Office 365 simulator), IMAP/SMTP, MCP or REST.',
      state: progress.connect,
      action: controller && runHref ? { to: `${runHref}/connect`, label: 'Connect your agent' } : null,
      roleNote: controller ? 'Start a run first.' : 'Connecting an agent needs a controller key.',
      concept: 'interfaces',
    },
    {
      n: 5,
      title: 'Advance time and let personas reply',
      why: 'Nothing happens on its own: advancing the virtual clock delivers scheduled mail and lets personas answer the agent.',
      state: progress.advance,
      action: runHref ? { to: runHref, label: 'Open the run' } : null,
      roleNote: 'Start a run first.',
      concept: 'virtual-clock',
    },
    {
      n: 6,
      title: 'Evaluate and read the results',
      why: 'Evaluation applies the answer key to what the agent did and explains every pass and failure with evidence.',
      state: progress.evaluate,
      action: evaluator && runHref ? { to: `${runHref}/evaluations`, label: 'Evaluate the run' } : null,
      roleNote: evaluator ? 'Start a run first.' : 'Evaluating needs an evaluator key.',
      concept: 'evaluation',
    },
    {
      n: 7,
      title: 'Compare builds',
      why: 'Run the same scenario with different versions of your agent and compare accuracy across repeated runs.',
      state: progress.compare,
      action: evaluator && scenarioKey ? { to: `/scenarios/${enc(scenarioKey)}/compare`, label: 'Compare builds' } : null,
      roleNote: evaluator ? 'Evaluate runs of two builds first.' : 'Comparing builds needs an evaluator key.',
      concept: 'agent-build',
    },
  ];
  const done = steps.filter((s) => s.state === 'done').length;
  const current = steps.find((s) => s.state !== 'done');

  return (
    <section aria-labelledby="gs-h">
      <h2 id="gs-h">Getting started</h2>
      <p data-testid="progress-text">
        {done} of {steps.length} steps done.
      </p>
      <progress max={steps.length} value={done} aria-label="Getting started progress" />
      <ol className="checklist" data-testid="checklist">
        {steps.map((s) => (
          <li
            key={s.n}
            className={`step step-${s.state}${s === current ? ' current' : ''}`}
            aria-current={s === current ? 'step' : undefined}
            data-testid={`step-${s.n}`}
          >
            <div className="step-head">
              <h3>
                {s.n}. {s.title}
              </h3>
              <span className={`badge badge-step-${s.state}`} data-testid={`step-${s.n}-state`}>
                {STATE_TEXT[s.state]}
              </span>
            </div>
            <p>{s.why}</p>
            <div className="row">
              {s.action ? (
                <Link className={`button${s === current ? ' primary' : ''}`} to={s.action.to}>
                  {s.action.label}
                </Link>
              ) : (
                <span className="muted">{s.roleNote}</span>
              )}
              {s.concept && <WhatIsThis term={s.concept} />}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Faq() {
  return (
    <section aria-labelledby="faq-h">
      <h2 id="faq-h">Common questions</h2>
      <details className="faq">
        <summary>Where do I create mailboxes for personas?</summary>
        <p>
          You don’t create them by hand. Personas are declared in the scenario; when a run starts, every persona gets its
          own simulated mailbox, and the scenario names the one the agent uses. See them in a run’s Mailboxes tab.
        </p>
      </details>
      <details className="faq">
        <summary>How do I connect through the GSuite or Office 365 simulator?</summary>
        <p>
          Start a run, then open <strong>Connect your agent</strong> on the run page. Choose Gmail API (GSuite simulator)
          or Microsoft Graph (Office 365 simulator) to get the base URL, the key to use as the access token and
          ready-to-paste code, then press Test connection.
        </p>
      </details>
      <details className="faq">
        <summary>Can mail leave the simulator?</summary>
        <p>No. Every interface delivers only to simulated mailboxes; outside addresses follow the scenario’s delivery rule.</p>
      </details>
    </section>
  );
}

/** Home: the getting-started checklist for operators; agents and visitors are sent on. */
export function HomePage() {
  usePageTitle('Home');
  const { session, probing } = useSession();
  if (!session && probing) return <Probing />;
  if (!session) return <Navigate to="/connect" replace />;
  if (session.role === 'agent') return <Navigate to="/agent-key" replace />;
  return (
    <>
      <h1>Test your email agent</h1>
      <PageIntro next={[{ to: '/concepts', label: 'Concepts in two minutes' }]}>
        <p>
          mailsim runs your agent against simulated people and mailboxes, then grades what it did. Follow the steps
          below; each one checks its own progress against the API.
        </p>
      </PageIntro>
      <Checklist role={session.role} />
      <Faq />
    </>
  );
}
