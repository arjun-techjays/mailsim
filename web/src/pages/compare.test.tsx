import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';

const KEY = 'finance.invoice-bank-change';

describe('Build comparison', () => {
  it('shows stats, deltas vs baseline, an accessible chart and per-assertion rates', async () => {
    const urls: string[] = [];
    server.events.on('request:start', ({ request }) => {
      urls.push(request.url);
    });
    const { user } = renderApp(`/scenarios/${KEY}/compare`, sessionFor('evaluator'));
    const table = await screen.findByTestId('comparison-table');
    const row = (b: string) => within(table).getByRole('row', { name: new RegExp(b) });
    expect(row('agent-v1')).toHaveTextContent('baseline');
    expect(row('agent-v2')).toHaveTextContent('81.0%');
    expect(row('agent-v2')).toHaveTextContent('+19.0 pp');
    expect(row('agent-v2')).toHaveTextContent('+30.0 pp');
    expect(row('agent-v3')).toHaveTextContent('n/a');
    expect(screen.getByRole('img', { name: /Mean accuracy by build/ })).toHaveAccessibleName(/agent-v2: 81\.0% \(10 runs\)/);
    expect(screen.getByTestId('per-assertion-table')).toHaveTextContent('no-unverified-approval');

    await user.type(screen.getByLabelText('Answer-key version'), '2');
    await user.type(screen.getByLabelText('Builds (comma-separated)'), 'agent-v2, agent-v3');
    await user.type(screen.getByLabelText('Baseline build'), 'agent-v2');
    await user.click(screen.getByRole('button', { name: 'Compare' }));
    await waitFor(() => expect(within(screen.getByTestId('comparison-table')).getAllByRole('row')).toHaveLength(3));
    expect(within(screen.getByTestId('comparison-table')).getByRole('row', { name: /agent-v3/ })).toHaveTextContent('-31.0 pp');
    expect(urls.at(-1)).toContain('answer_key_version=2&builds=agent-v2&builds=agent-v3&baseline=agent-v2');
    server.events.removeAllListeners();
  });

  it('errors, empty builds and missing per-assertion data', async () => {
    server.use(
      http.get('*/v1/scenarios/:key/accuracy', ({ request }) =>
        new URL(request.url).searchParams.get('baseline') === 'none'
          ? HttpResponse.json({ scenario_key: KEY, answer_key_version: 1, builds: {} })
          : HttpResponse.json({
              scenario_key: KEY,
              answer_key_version: 1,
              builds: { b1: { runs: 2, mean_accuracy: 0.5, variance: 0.1, stdev: 0.3, pass_rate: 0.5 } },
            }),
      ),
    );
    const { user } = renderApp(`/scenarios/${KEY}/compare`, sessionFor('evaluator'));
    expect(await screen.findByText('No per-assertion data.')).toBeInTheDocument();
    expect(screen.getByText(/baseline:/)).toHaveTextContent('none');
    expect(within(screen.getByTestId('comparison-table')).getByRole('row', { name: /b1/ })).toHaveTextContent('n/a');
    await user.type(screen.getByLabelText('Baseline build'), 'none');
    await user.click(screen.getByRole('button', { name: 'Compare' }));
    expect(await screen.findByText('No evaluated runs for these builds.')).toBeInTheDocument();
  });

  it('API errors are displayed', async () => {
    renderApp('/scenarios/other/compare', sessionFor('evaluator'));
    expect(await screen.findByText(/Could not load comparison/)).toBeInTheDocument();
  });

  it('is gated for controllers', () => {
    renderApp(`/scenarios/${KEY}/compare`, sessionFor('controller'));
    expect(screen.getByTestId('role-gate')).toHaveTextContent('Build comparison requires an evaluator key');
  });
});
