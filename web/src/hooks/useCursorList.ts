import { useInfiniteQuery, type QueryKey } from '@tanstack/react-query';

export interface CursorPage<T> {
  items: T[];
  next_cursor?: string | null;
}

/**
 * Accumulates cursor-paginated results ({items, next_cursor}) and exposes "load more".
 * The first page is requested with cursor=null; pagination stops when next_cursor is null.
 */
export function useCursorList<T>(
  queryKey: QueryKey,
  fetchPage: (cursor: string | null) => Promise<CursorPage<T>>,
  enabled = true,
) {
  const q = useInfiniteQuery({
    queryKey,
    enabled,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  return {
    items: q.data?.pages.flatMap((p) => p.items) ?? [],
    pages: q.data?.pages.length ?? 0,
    hasMore: q.hasNextPage,
    loadMore: () => void q.fetchNextPage(),
    isLoading: q.isPending && enabled,
    isFetchingMore: q.isFetchingNextPage,
    error: q.error,
    refetch: () => void q.refetch(),
  };
}
