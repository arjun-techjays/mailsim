// Plain-language explanations shown next to the builder's choices. Values come from
// GET /v1/meta/authoring; FALLBACK_META is used (with a notice) if that call fails.

export interface BuilderMeta {
  agent_operations: string[];
  trigger_types: string[];
  trigger_actions: string[];
  external_delivery_modes: string[];
  behavior_modes: string[];
}

export const FALLBACK_META: BuilderMeta = {
  agent_operations: ['read', 'search', 'reply', 'send', 'forward', 'insert', 'move', 'copy', 'label', 'flag', 'draft', 'delete'],
  trigger_types: ['at', 'after_seconds', 'after_agent_action'],
  trigger_actions: ['send_reply', 'reply', 'send_message', 'send', 'read', 'search', 'move', 'label', 'flag', 'draft', 'delete', 'forward', 'insert', 'copy'],
  external_delivery_modes: ['deny', 'sink', 'event'],
  behavior_modes: ['silent', 'scripted', 'llm'],
};

export const OPERATION_HELP: Record<string, string> = {
  read: 'Open and read messages',
  search: 'Search the mailbox',
  reply: 'Reply to a message',
  send: 'Send a new message',
  forward: 'Forward a message',
  insert: 'Insert a message directly into a folder',
  move: 'Move messages between folders',
  copy: 'Copy messages to another folder',
  label: 'Add or remove labels',
  flag: 'Flag, star or mark messages read/unread',
  draft: 'Create and edit drafts',
  delete: 'Delete messages',
  external_delivery: 'Send mail to an address outside the simulation (never actually delivered)',
};

export const TRIGGER_HELP: Record<string, string> = {
  at: 'At a fixed virtual date and time.',
  after_seconds: 'A number of virtual seconds after the run starts.',
  after_agent_action: 'When the agent first performs an action (for example, after it sends a reply), plus an optional delay.',
};

export const DELIVERY_HELP: Record<string, string> = {
  deny: 'Refuse: mail to addresses outside the scenario is rejected and recorded. Recommended.',
  sink: 'Accept and keep it in a sink inside the simulator (nothing leaves the simulator).',
  event: 'Accept and record it only as an event (nothing leaves the simulator).',
};

export const MODE_HELP: Record<string, string> = {
  silent: 'Never replies. Use for people the agent only receives mail from or writes to.',
  scripted: 'Replies with fixed text when an incoming message matches a rule. Deterministic.',
  llm: 'Replies using the configured language model, in the given style. Outputs are recorded so runs can be replayed.',
};

export function label(map: Record<string, string>, value: string): string {
  return map[value] ?? '';
}
