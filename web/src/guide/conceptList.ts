// The vocabulary of the dashboard, in plain language (Concepts page + "What is this?" hints).

export type ConceptId =
  | 'scenario'
  | 'persona'
  | 'mailbox'
  | 'run'
  | 'agent-key'
  | 'answer-key'
  | 'checks'
  | 'virtual-clock'
  | 'interfaces'
  | 'evaluation'
  | 'agent-build';

export interface Concept {
  id: ConceptId;
  term: string;
  question: string;
  short: string;
  more: string;
}

export const CONCEPTS: readonly Concept[] = [
  {
    id: 'scenario',
    question: 'What is a scenario?',
    term: 'Scenario',
    short: 'A test case for an email agent: who is involved, what is already in the mailboxes, and what happens over time.',
    more: 'A scenario is a versioned document (YAML or JSON) with personas, seeded messages, timed events and the agent’s permissions. Publishing a change creates a new version; runs always record the version they used.',
  },
  {
    id: 'persona',
    question: 'What is a persona?',
    term: 'Persona',
    short: 'A simulated person (a vendor, a colleague, a customer) with their own mailbox.',
    more: 'Personas are declared in the scenario. Each one gets a mailbox when a run starts; you do not create mailboxes by hand. Personas can reply to the agent, by script or through an LLM, when the virtual clock advances.',
  },
  {
    id: 'mailbox',
    question: 'What is a mailbox?',
    term: 'Mailbox',
    short: 'A simulated inbox, created for every persona when a run starts. One of them belongs to the agent under test.',
    more: 'The scenario’s agent.mailbox names the persona whose mailbox the agent uses. Mailboxes have the usual folders (Inbox, Sent, Drafts, Trash, Archive) and exist only inside their run: nothing is ever delivered outside the simulator.',
  },
  {
    id: 'run',
    question: 'What is a run?',
    term: 'Run',
    short: 'One isolated execution of a scenario version, with its own mailboxes, clock and event log.',
    more: 'A run goes READY → RUNNING → PASSED/FAILED (after evaluation), or CANCELLED. You can reset it (new epoch, same seed) or replay it to prove it is deterministic.',
  },
  {
    id: 'agent-key',
    question: 'What is an agent key?',
    term: 'Agent key',
    short: 'The only credential you give the agent under test. It opens exactly one mailbox of one run.',
    more: 'It is shown once, when the run is created or a new key is minted. It works on every agent interface (as a bearer token, an OAuth access token, or an IMAP/SMTP password) and never grants operator access. Minting a new key revokes the run’s earlier agent keys.',
  },
  {
    id: 'answer-key',
    question: 'What is an answer key?',
    term: 'Answer key',
    short: 'The hidden grading rules for a scenario: what a good agent should and should not do.',
    more: 'Answer keys are versioned separately from scenarios and are visible only to evaluators. They are never shown to the agent, in any message, header or API response.',
  },
  {
    id: 'checks',
    question: 'What are checks on / off?',
    term: 'Checks on / off',
    short: 'Whether the answer key’s deterministic checks run when a run is evaluated.',
    more: 'With checks off, only rubric (judge) assertions are graded. The choice is stored with the run as an evaluation preference and can be overridden when you evaluate; the agent never sees it.',
  },
  {
    id: 'virtual-clock',
    question: 'What is the virtual clock?',
    term: 'Virtual clock',
    short: 'The run’s own time. It moves only when you advance it, so runs are repeatable.',
    more: 'Advancing the clock fires scheduled events (new mail, follow-ups) and lets personas reply. Real time passing does nothing.',
  },
  {
    id: 'interfaces',
    question: 'What are interfaces?',
    term: 'Interfaces',
    short: 'The ways an agent can reach its mailbox: Gmail API, Microsoft Graph, IMAP/SMTP, MCP (HTTP or stdio) and REST.',
    more: 'All interfaces share the same mailbox state and the same rules (permissions, action budget, delivery policy), so you can test an agent unmodified with the client library it already uses.',
  },
  {
    id: 'evaluation',
    question: 'What is an evaluation?',
    term: 'Evaluation',
    short: 'Applying an answer key to a finished (or running) run to get a verdict and accuracy.',
    more: 'The agent is not re-run: evaluation reads the recorded events and mailbox state. You can evaluate the same run with different answer-key versions.',
  },
  {
    id: 'agent-build',
    question: 'What is an agent build?',
    term: 'Agent build',
    short: 'A label for the version of your agent (for example agent-v2), set when you start a run.',
    more: 'Compare builds aggregates accuracy over repeated runs per build, so you can see whether a change to your agent helped.',
  },
];

export function concept(id: ConceptId): Concept {
  return CONCEPTS.find((c) => c.id === id) as Concept;
}
