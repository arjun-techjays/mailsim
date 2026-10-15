// Email HTML is untrusted (it may come from an adversarial scenario or from the agent).
// It is NEVER inserted into the dashboard DOM. When the user asks for the HTML view it is
//  1. sanitized in an inert DOMParser document (no scripts run, nothing is fetched there),
//  2. rendered inside an iframe with an EMPTY sandbox attribute (no scripts, no same-origin,
//     no forms, no popups, no top navigation) via srcdoc,
//  3. under a CSP that blocks every network fetch (tracking pixels, remote CSS, fonts).
export const EMAIL_CSP =
  "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; form-action 'none'; base-uri 'none'";

// SVG animation elements are removed too: SMIL can re-add an href without any script.
const REMOVE =
  'script, noscript, template, meta, base, link, object, embed, applet, iframe, frame, frameset, portal, animate, set, animateMotion, animateTransform';
const URL_ATTRS = ['href', 'src', 'srcset', 'action', 'formaction', 'background', 'poster', 'cite', 'longdesc', 'ping', 'xlink:href', 'lowsrc', 'dynsrc'];

/** Returns sanitized body markup: no active/navigating elements, no handlers, no live URLs. */
export function sanitizeEmailHtml(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  doc.querySelectorAll(REMOVE).forEach((el) => el.remove());
  doc.querySelectorAll('form').forEach((form) => form.replaceWith(...Array.from(form.childNodes)));
  doc.querySelectorAll('*').forEach((el) => {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on') || name === 'target' || name === 'formtarget') el.removeAttribute(attr.name);
    }
    const link = el.tagName === 'A' || el.tagName === 'a'; // HTML <A> or SVG <a>
    const href = el.getAttribute('href') ?? el.getAttribute('xlink:href');
    if (link && href) {
      const shown = doc.createElement('span');
      shown.className = 'mailsim-url';
      shown.textContent = ` [${href}]`;
      el.after(shown);
    }
    const keepImage = el.tagName === 'IMG' && /^data:image\//i.test(el.getAttribute('src') ?? '');
    for (const name of URL_ATTRS) {
      if (name === 'src' && keepImage) continue;
      el.removeAttribute(name);
    }
  });
  return doc.body.innerHTML;
}

export function wrapHtml(html: string): string {
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    `<meta http-equiv="Content-Security-Policy" content="${EMAIL_CSP}">` +
    '<style>body{font-family:system-ui,sans-serif;margin:8px;color:#111;background:#fff}.mailsim-url{color:#555}</style>' +
    `</head><body>${sanitizeEmailHtml(html)}</body></html>`
  );
}
