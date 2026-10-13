"use client";

import { useCallback, useEffect, useState } from "react";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8100").replace(/\/$/, "");
export const N8N_URL = (process.env.NEXT_PUBLIC_N8N_URL ?? "http://localhost:5678").replace(/\/$/, "");
const TOKEN = process.env.NEXT_PUBLIC_API_TOKEN ?? "relay-local-api-token";

/** status 0 = the API could not be reached at all (network error, CORS, server down). */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(TOKEN ? { "X-Relay-Token": TOKEN } : {}), ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, `Relay API not reachable at ${API_URL}`);
  }
  if (!response.ok) {
    let detail = response.statusText || `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export function post<T>(path: string, body: unknown = {}): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

interface State<T> {
  path: string | null;
  data: T | null;
  error: string | null;
  status: number | null;
}

/**
 * GET `path` and keep it fresh. With `interval` (ms) the request repeats while the tab is visible; a failed
 * refresh keeps the last good data and reports the error. `path = null` disables the request.
 */
export function useApi<T>(
  path: string | null,
  { interval }: { interval?: number } = {},
): {
  data: T | null;
  error: string | null;
  status: number | null;
  loading: boolean;
  reload: () => void;
} {
  const [state, setState] = useState<State<T>>({ path: null, data: null, error: null, status: null });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    const load = () => {
      api<T>(path)
        .then((value) => {
          if (!cancelled) setState({ path, data: value, error: null, status: 200 });
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          const status = err instanceof ApiError ? err.status : 0;
          setState((prev) => ({ path, data: prev.path === path ? prev.data : null, error: errorText(err), status }));
        });
    };
    load();
    if (!interval) {
      return () => {
        cancelled = true;
      };
    }
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, interval);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [path, tick, interval]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const current = state.path === path;
  return {
    data: current ? state.data : null,
    error: current ? state.error : null,
    status: current ? state.status : null,
    loading: path !== null && !current,
    reload,
  };
}

export const POLL_MS = 5000;
