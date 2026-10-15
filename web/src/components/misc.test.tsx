import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BarChart } from './BarChart';
import { CopyButton } from './CopyButton';
import { address, addresses, bytes, delta, num, pct, splitList } from './format';
import { JsonBlock } from './JsonBlock';
import { Badge, Empty, Loading } from './Status';

describe('format helpers', () => {
  it('formats values', () => {
    expect(pct(0.5)).toBe('50.0%');
    expect(pct(null)).toBe('n/a');
    expect(delta(0.19)).toBe('+19.0 pp');
    expect(delta(-0.31)).toBe('-31.0 pp');
    expect(delta(0)).toBe('0.0 pp');
    expect(delta(undefined)).toBe('n/a');
    expect(num(0.12345)).toBe('0.123');
    expect(num(null)).toBe('n/a');
    expect(bytes(null)).toBe('unknown');
    expect(bytes(10)).toBe('10 B');
    expect(bytes(2048)).toBe('2.0 KB');
    expect(bytes(3 * 1024 * 1024)).toBe('3.0 MB');
    expect(address(null)).toBe('');
    expect(address({ email: 'a@x' })).toBe('a@x');
    expect(address({ email: 'a@x', name: 'A' })).toBe('A <a@x>');
    expect(addresses(undefined)).toBe('');
    expect(addresses([{ email: 'a@x' }, { email: 'b@x' }])).toBe('a@x, b@x');
    expect(splitList(' a@x, ,b@x\nc@x ')).toEqual(['a@x', 'b@x', 'c@x']);
  });
});

describe('small components', () => {
  it('Loading / Empty / Badge', () => {
    render(
      <>
        <Loading />
        <Empty>nothing</Empty>
        <Badge value="PASS" />
        <Badge value="x" kind="FAIL" />
      </>,
    );
    expect(screen.getByRole('status')).toHaveTextContent('Loading…');
    expect(screen.getByTestId('empty-state')).toHaveTextContent('nothing');
    expect(screen.getByText('PASS')).toHaveClass('badge-pass');
    expect(screen.getByText('x')).toHaveClass('badge-fail');
  });

  it('JsonBlock collapses objects and shows a dash for empty ones', () => {
    const { container, rerender } = render(<JsonBlock value={{ a: 1 }} label="Payload" />);
    expect(screen.getByText('Payload')).toBeInTheDocument();
    expect(container.querySelector('pre')).toHaveTextContent('"a": 1');
    rerender(<JsonBlock value={{}} label="Payload" />);
    expect(screen.getByText('—')).toBeInTheDocument();
    rerender(<JsonBlock value={null} label="Payload" />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('BarChart exposes a text alternative with every value', () => {
    render(
      <BarChart
        title="Accuracy"
        bars={[
          { label: 'a', value: 0.5, note: '3 runs' },
          { label: 'b', value: null },
          { label: 'c', value: 1.7 },
        ]}
        format={pct}
      />,
    );
    const img = screen.getByRole('img', { name: /Accuracy/ });
    expect(img).toHaveAccessibleName(/a: 50\.0% \(3 runs\); b: n\/a; c: 170\.0%/);
    expect(screen.getAllByTestId('chart-bar')).toHaveLength(3);
  });

  it('CopyButton reports success and failure', async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, 'writeText');
    render(<CopyButton value="k" label="Copy key" />);
    await user.click(screen.getByRole('button', { name: 'Copy key' }));
    expect(write).toHaveBeenCalledWith('k');
    expect(screen.getByRole('status')).toHaveTextContent('Copied to clipboard.');
    write.mockRejectedValueOnce(new Error('denied'));
    await user.click(screen.getByRole('button', { name: 'Copy key' }));
    expect(screen.getByRole('status')).toHaveTextContent('Copy failed');
  });
});

describe('PageIntro', () => {
  it('renders the explanation and next-step links', async () => {
    const { render, screen } = await import('@testing-library/react');
    const { MemoryRouter } = await import('react-router-dom');
    const { PageIntro } = await import('./PageIntro');
    render(
      <MemoryRouter>
        <PageIntro next={[{ to: '/runs', label: 'Start a run' }]}>Scenarios describe a world.</PageIntro>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('page-intro')).toHaveTextContent('Scenarios describe a world.');
    expect(screen.getByRole('link', { name: 'Start a run' })).toHaveAttribute('href', '/runs');
    render(
      <MemoryRouter>
        <PageIntro>Alone</PageIntro>
      </MemoryRouter>,
    );
    expect(screen.getAllByTestId('page-intro')[1]?.querySelector('nav')).toBeNull();
  });
});
