import { useId } from 'react';

export interface Bar {
  label: string;
  value: number | null;
  note?: string;
}

/**
 * Accessible horizontal bar chart for 0..1 values. The SVG is an image with a text
 * alternative listing every value; the same data is always rendered as a table too.
 */
export function BarChart({ title, bars, format }: { title: string; bars: Bar[]; format: (v: number | null) => string }) {
  const id = useId();
  const rowH = 28;
  const labelW = 120;
  const chartW = 300;
  const height = bars.length * rowH + 8;
  const summary = bars.map((b) => `${b.label}: ${format(b.value)}${b.note ? ` (${b.note})` : ''}`).join('; ');
  return (
    <figure className="chart">
      <figcaption id={`${id}-t`}>{title}</figcaption>
      <svg
        role="img"
        aria-labelledby={`${id}-t ${id}-d`}
        viewBox={`0 0 ${labelW + chartW + 70} ${height}`}
        width="100%"
        style={{ maxWidth: 560 }}
      >
        <desc id={`${id}-d`}>{summary}</desc>
        {bars.map((b, i) => {
          const y = i * rowH + 4;
          const w = b.value == null ? 0 : Math.max(0, Math.min(1, b.value)) * chartW;
          return (
            <g key={b.label} data-testid="chart-bar">
              <text x={labelW - 8} y={y + 17} textAnchor="end" fontSize="13" fill="currentColor">
                {b.label}
              </text>
              <rect x={labelW} y={y} width={chartW} height={rowH - 8} fill="var(--chart-track)" />
              <rect x={labelW} y={y} width={w} height={rowH - 8} fill="var(--chart-bar)" />
              <text x={labelW + chartW + 6} y={y + 17} fontSize="13" fill="currentColor">
                {format(b.value)}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
