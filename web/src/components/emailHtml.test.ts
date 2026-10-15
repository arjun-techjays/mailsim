import { sanitizeEmailHtml, wrapHtml } from './emailHtml';

function parse(html: string): Document {
  return new DOMParser().parseFromString(sanitizeEmailHtml(html), 'text/html');
}

describe('sanitizeEmailHtml', () => {
  it('drops meta refresh, base, link, object, embed, iframe, frame and script', () => {
    const doc = parse(
      '<meta http-equiv="refresh" content="0;url=https://evil.example/">' +
        '<base href="https://evil.example/"><link rel="stylesheet" href="https://evil.example/x.css">' +
        '<object data="x"></object><embed src="x"><iframe src="https://evil.example"></iframe>' +
        '<frameset><frame src="x"></frameset><script>parent.__mailsimPwned=1</script><noscript>n</noscript><p>ok</p>',
    );
    expect(doc.querySelector('meta, base, link, object, embed, iframe, frame, script, noscript')).toBeNull();
    expect(doc.body.textContent).toContain('ok');
  });

  it('strips targets and handlers and shows link URLs as text instead of navigating', () => {
    const doc = parse(
      '<a href="https://evil.example/pay" target="_self" onclick="x()">Pay</a>' +
        '<a name="anchor">no href</a><div onmouseover="x()" style="color:red">d</div>',
    );
    const a = doc.querySelector('a') as HTMLAnchorElement;
    expect(a.hasAttribute('href')).toBe(false);
    expect(a.hasAttribute('target')).toBe(false);
    expect(a.hasAttribute('onclick')).toBe(false);
    expect(doc.querySelector('.mailsim-url')?.textContent).toBe(' [https://evil.example/pay]');
    expect(doc.querySelectorAll('.mailsim-url')).toHaveLength(1);
    expect(doc.querySelector('div')?.getAttribute('onmouseover')).toBeNull();
    expect(doc.querySelector('div')?.getAttribute('style')).toBe('color:red');
  });

  it('unwraps forms and removes form/URL attributes; keeps only data: images', () => {
    const doc = parse(
      '<form action="https://evil.example/steal"><input name="pw" formaction="https://evil.example" formtarget="_top"><button>Go</button></form>' +
        '<img src="https://tracker.example/p.gif" srcset="https://t/1x.gif 1x"><img src="data:image/png;base64,AAAA"><img alt="no src">' +
        '<table background="https://t/bg.png"><tr><td>c</td></tr></table><video poster="https://t/p.png"></video>',
    );
    expect(doc.querySelector('form')).toBeNull();
    const input = doc.querySelector('input') as HTMLInputElement;
    expect(input.hasAttribute('formaction')).toBe(false);
    expect(input.hasAttribute('formtarget')).toBe(false);
    const [remote, inline] = Array.from(doc.querySelectorAll('img'));
    expect(remote?.hasAttribute('src')).toBe(false);
    expect(remote?.hasAttribute('srcset')).toBe(false);
    expect(inline?.getAttribute('src')).toBe('data:image/png;base64,AAAA');
    expect(doc.querySelector('table')?.hasAttribute('background')).toBe(false);
    expect(doc.querySelector('video')?.hasAttribute('poster')).toBe(false);
  });

  it('wrapHtml embeds sanitized markup under the email CSP, without <base>', () => {
    const out = wrapHtml('<meta http-equiv="refresh" content="0;url=https://evil.example/"><a href="x" target="_self">l</a>');
    expect(out).not.toContain('refresh');
    expect(out).not.toContain('<base');
    expect(out).not.toContain('target=');
    expect(out).toContain("default-src 'none'");
  });
});
