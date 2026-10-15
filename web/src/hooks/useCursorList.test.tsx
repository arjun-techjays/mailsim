import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { makeQueryClient } from '../queryClient';
import { useCursorList, type CursorPage } from './useCursorList';

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

describe('useCursorList', () => {
  it('starts with a null cursor, accumulates pages and stops at next_cursor=null', async () => {
    const pages: Record<string, CursorPage<number>> = {
      start: { items: [1, 2], next_cursor: 'c2' },
      c2: { items: [3, 4], next_cursor: 'c3' },
      c3: { items: [5], next_cursor: null },
    };
    const fetchPage = vi.fn(async (cursor: string | null) => pages[cursor ?? 'start'] as CursorPage<number>);
    const { result } = renderHook(() => useCursorList(['nums'], fetchPage), { wrapper });
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.items).toEqual([1, 2]));
    expect(fetchPage).toHaveBeenNthCalledWith(1, null);
    expect(result.current.hasMore).toBe(true);

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.items).toEqual([1, 2, 3, 4]));
    expect(fetchPage).toHaveBeenNthCalledWith(2, 'c2');
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.items).toEqual([1, 2, 3, 4, 5]));
    expect(result.current.hasMore).toBe(false);
    expect(result.current.pages).toBe(3);

    act(() => result.current.refetch());
    await waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(6));
  });

  it('treats a missing next_cursor as the last page', async () => {
    const { result } = renderHook(() => useCursorList(['one'], async () => ({ items: ['a'] })), { wrapper });
    await waitFor(() => expect(result.current.items).toEqual(['a']));
    expect(result.current.hasMore).toBe(false);
  });

  it('exposes errors and keeps loaded items', async () => {
    let calls = 0;
    const { result } = renderHook(
      () =>
        useCursorList(['err'], async () => {
          calls += 1;
          if (calls > 1) throw new Error('page 2 failed');
          return { items: ['a'], next_cursor: 'n' };
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.items).toEqual(['a']));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error));
    expect(result.current.items).toEqual(['a']);
  });

  it('does not fetch while disabled', () => {
    const fetchPage = vi.fn();
    const { result } = renderHook(() => useCursorList(['off'], fetchPage, false), { wrapper });
    expect(fetchPage).not.toHaveBeenCalled();
    expect(result.current.isLoading).toBe(false);
    expect(result.current.items).toEqual([]);
  });
});
