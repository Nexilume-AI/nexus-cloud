import { useEffect, useState } from "react";

export function useCatalogSearch(value: string) {
  const [query, setQuery] = useState(value);
  useEffect(() => { const timer = window.setTimeout(() => setQuery(value.trim()), 250); return () => window.clearTimeout(timer); }, [value]);
  return query;
}

export type CursorPage<T> = { items: T[]; next_cursor: string | null; has_more: boolean; limit: number; as_of?: string; summary?: { actions: string[]; resources: string[] } };

/** Keep only the current page's rows. A filter/context change resets synchronously. */
export function useCursorPage(scope: unknown) {
  const key = JSON.stringify(scope);
  const [state, setState] = useState<{ key: string; cursors: string[] }>({ key, cursors: [""] });
  const cursors = state.key === key ? state.cursors : [""];
  return {
    cursor: cursors[cursors.length - 1],
    page: cursors.length,
    previous: () => setState({ key, cursors: cursors.slice(0, -1).length ? cursors.slice(0, -1) : [""] }),
    next: (cursor: string | null | undefined) => { if (cursor) setState({ key, cursors: [...cursors, cursor] }); },
    reset: () => setState({ key, cursors: [""] }),
  };
}
