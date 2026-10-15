import { splitList } from '../components/format';

export interface Fields {
  mailbox: string;
  from: string;
  to: string;
  cc: string;
  subject: string;
  text: string;
  html: string;
  eventId: string;
}

export type Errors = Partial<Record<keyof Fields, string>>;

export function validateInject(f: Fields): Errors {
  const errors: Errors = {};
  if (!f.mailbox.trim()) errors.mailbox = 'Choose the mailbox to deliver to.';
  if (!f.from.trim()) errors.from = 'Sender address is required.';
  if (splitList(f.to).length === 0) errors.to = 'At least one recipient is required.';
  if (!f.text.trim() && !f.html.trim()) errors.text = 'Provide a text body (or an HTML body).';
  return errors;
}
