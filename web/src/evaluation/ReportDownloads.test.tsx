import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';

const readBlob = (b: Blob | undefined) =>
  new Promise<string>((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.readAsText(b as Blob);
  });

describe('Report downloads', () => {
  it('fetches each export with the key and saves it via a Blob URL', async () => {
    const created: Blob[] = [];
    URL.createObjectURL = vi.fn((b: Blob) => {
      created.push(b);
      return 'blob:mock';
    });
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const auth: (string | null)[] = [];
    server.events.on('request:start', ({ request }) => {
      if (request.url.includes('/report')) auth.push(request.headers.get('authorization'));
    });
    const { user } = renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
    await screen.findByTestId('evaluation-report');

    await user.click(screen.getByRole('button', { name: 'Download JUnit XML' }));
    await waitFor(() =>
      expect(screen.getByTestId('report-download-status')).toHaveTextContent('Downloaded finance.invoice-bank-change-run_demo-eval_1.xml'),
    );
    expect(created[0]?.type).toBe('application/octet-stream');
    expect(await readBlob(created[0])).toContain('<testsuite');

    await user.click(screen.getByRole('button', { name: 'Download Markdown' }));
    await waitFor(() => expect(screen.getByTestId('report-download-status')).toHaveTextContent('finance.invoice-bank-change-run_demo-eval_1.md'));
    await user.click(screen.getByRole('button', { name: 'Download JSON' }));
    await waitFor(() => expect(screen.getByTestId('report-download-status')).toHaveTextContent('run_demo-eval_1.json'));
    expect(await readBlob(created[2])).toContain('"evaluation_id":"eval_1"');
    expect(click).toHaveBeenCalledTimes(3);
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock'), { timeout: 2500 });
    expect(auth.every((a) => a === 'Bearer mk_evaluator')).toBe(true);
    server.events.removeAllListeners();
  });

  it('shows errors and a busy label', async () => {
    let release: () => void = () => {};
    server.use(
      http.get('*/v1/runs/:id/evaluations/:eid/report', async () => {
        await new Promise<void>((r) => (release = r));
        return HttpResponse.json({ error: { code: 'INTERNAL', message: 'x', details: {}, request_id: 'r' } }, { status: 500 });
      }),
    );
    const { user } = renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
    await screen.findByTestId('evaluation-report');
    await user.click(screen.getByRole('button', { name: 'Download JSON' }));
    expect(await screen.findByRole('button', { name: 'Downloading…' })).toBeDisabled();
    release();
    expect(await screen.findByText(/Could not download report/)).toBeInTheDocument();
  });

  it('falls back to a generic content type', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    server.use(http.get('*/v1/runs/:id/evaluations/:eid/report', () => new HttpResponse(new Blob(['raw']))));
    const { user } = renderApp('/runs/run_demo/evaluations', sessionFor('evaluator'));
    await screen.findByTestId('evaluation-report');
    await user.click(screen.getByRole('button', { name: 'Download Markdown' }));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
  });
});
