import { formatVirtual, formatVirtualShort } from './mailFormat';

export function VirtualTime({ value, short = false }: { value: string | null | undefined; short?: boolean }) {
  if (!value) return <>—</>;
  return (
    <time dateTime={value} title={value}>
      {short ? formatVirtualShort(value) : formatVirtual(value)}
    </time>
  );
}
