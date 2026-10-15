// Email HTML is untrusted (it may come from an adversarial scenario or from the agent).
// It is NEVER inserted into the dashboard DOM. When the user asks for the HTML view it is
// rendered inside an iframe with an EMPTY sandbox attribute (no scripts, no same-origin,
// no forms, no popups, no top navigation) via srcdoc, plus a CSP that blocks every
// network fetch (tracking pixels, remote CSS, fonts).
export const EMAIL_CSP =
  "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; form-action 'none'";

export function wrapHtml(html: string): string {
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    `<meta http-equiv="Content-Security-Policy" content="${EMAIL_CSP}">` +
    '<base target="_blank"><style>body{font-family:system-ui,sans-serif;margin:8px;color:#111;background:#fff}</style>' +
    `</head><body>${html}</body></html>`
  );
}
