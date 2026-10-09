/**
 * Small data-fetching hook with explicit loading/error/empty states.
 *
 * Every page uses this so that "loading", "failed" and "empty" are rendered from
 * the request's real state. A page never shows placeholder data while a request is
 * in flight, which is what keeps the dashboard numbers honest.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
  setData: (data: T | null) => void;
}

export function useAsync<T>(
  loader: () => Promise<T>,
  /**
   * Stable identity for the request. Include every value the loader closes over that
   * can change, but keep the ARRAY LENGTH constant: React requires a fixed dependency
   * count, so a caller whose deps array changed length would throw at runtime. The
   * simplest safe form is a single primitive key (e.g. `[sessionId]`), which is what
   * the call sites use.
   */
  deps: readonly unknown[] = [],
  options: { enabled?: boolean } = {},
): AsyncState<T> {
  const enabled = options.enabled ?? true;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [nonce, setNonce] = useState(0);
  const mounted = useRef(true);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  // Serialise the caller's deps into one primitive so the effect's dependency array
  // has a fixed length no matter how many deps a caller passes. Without this, a
  // varying-length `deps` array changes the hook's hook-order dependency count between
  // renders and React throws.
  const depsKey = JSON.stringify(deps.map((value) => (value === undefined ? null : value)));

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    // Clear the stale payload so a page cannot render the previous request's data
    // beside a failure notice for the current one.
    setData(null);
    loaderRef
      .current()
      .then((result) => {
        if (cancelled || !mounted.current) return;
        setData(result);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled || !mounted.current) return;
        setError(
          cause instanceof ApiError
            ? cause
            : new ApiError(0, "unknown_error", String(cause)),
        );
      })
      .finally(() => {
        if (cancelled || !mounted.current) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // `depsKey` is the serialised caller deps; `nonce` backs `reload()`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, nonce, depsKey]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  return { data, error, loading, reload, setData };
}

/** Format a probability as a percentage, or an explicit placeholder. */
export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}
