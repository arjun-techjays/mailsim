import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EMAIL_CSP, wrapHtml } from './emailHtml';
import { SafeEmailBody } from './SafeEmailBody';

const EVIL =
  '<p>Hello</p><script>window.__pwned = "script"</script><img src="x" onerror="window.__pwned = \'img\'">' +
  '<iframe src="javascript:window.__pwned=1"></iframe>';

declare global {
  interface Window {
    __pwned?: string;
  }
}

describe('SafeEmailBody', () => {
  afterEach(() => {
    delete window.__pwned;
  });

  it('shows the text part by default and never injects HTML into the document', () => {
    render(<SafeEmailBody text="Plain hello" html={EVIL} />);
    expect(screen.getByTestId('email-text')).toHaveTextContent('Plain hello');
    expect(screen.queryByTestId('email-html-frame')).toBeNull();
    expect(document.querySelectorAll('script')).toHaveLength(0);
    expect(document.body.innerHTML).not.toContain('onerror');
  });

  it('renders HTML only inside an empty-sandbox iframe via srcdoc; scripts never execute', async () => {
    render(<SafeEmailBody text="Plain hello" html={EVIL} />);
    await userEvent.click(screen.getByRole('button', { name: 'Show HTML (sandboxed)' }));
    const frame = screen.getByTestId('email-html-frame') as HTMLIFrameElement;
    expect(frame.tagName).toBe('IFRAME');
    expect(frame.getAttribute('sandbox')).toBe('');
    expect(frame.hasAttribute('src')).toBe(false);
    expect(frame.getAttribute('srcdoc')).toContain(EMAIL_CSP);
    expect(frame.getAttribute('referrerpolicy')).toBe('no-referrer');
    // nothing from the email leaked into the host document as live markup
    expect(document.querySelectorAll('script')).toHaveLength(0);
    expect(document.querySelectorAll('img')).toHaveLength(0);
    expect(window.__pwned).toBeUndefined();

    await userEvent.click(screen.getByRole('button', { name: 'Show plain text' }));
    expect(screen.getByTestId('email-text')).toBeInTheDocument();
  });

  it('defaults to the sandboxed HTML view when there is no text part', () => {
    render(<SafeEmailBody text={null} html="<b>only html</b>" />);
    expect(screen.getByTestId('email-html-frame')).toHaveAttribute('sandbox', '');
    expect(screen.getByText(/no plain-text part/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('handles messages without any body', () => {
    render(<SafeEmailBody text="" html={null} />);
    expect(screen.getByText('This message has no body.')).toBeInTheDocument();
  });

  it('wraps HTML with a restrictive CSP', () => {
    const doc = wrapHtml('<p>x</p>');
    expect(doc).toContain('Content-Security-Policy');
    expect(EMAIL_CSP).toContain("default-src 'none'");
    expect(EMAIL_CSP).not.toContain('script-src');
  });
});
