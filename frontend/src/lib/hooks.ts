"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "./api";

export function useResource<T>(path: string | null, version = 0, optional = false) {
  const [reloadId, setReloadId] = useState(0);
  const [state, setState] = useState<{ path: string | null; data: T | null; loading: boolean; error: Error | null }>({ path, data: null, loading: true, error: null });
  const reload = useCallback(() => setReloadId((value) => value + 1), []);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    setState((old) => ({ path, data: old.path === path ? old.data : null, loading: true, error: null }));
    request<T>(path, { signal: controller.signal }).then((data) => {
      if (!controller.signal.aborted) setState({ path, data, loading: false, error: null });
    }).catch((error) => {
      if (!controller.signal.aborted) setState({ path, data: null, loading: false,
        error: optional && error instanceof ApiError && error.status === 404 ? null : error });
    });
    return () => controller.abort();
  }, [path, version, reloadId, optional]);
  return { ...state, data: state.path === path ? state.data : null, reload };
}
export function useMutation() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [success, setSuccess] = useState("");
  async function run<T>(operation: () => Promise<T>, onSuccess?: (value: T) => void, message = "Changes saved.") {
    setBusy(true); setError(null); setSuccess("");
    try { const value = await operation(); onSuccess?.(value); setSuccess(message); return true; }
    catch (failure) { setError(failure instanceof Error ? failure : new Error("The action could not be completed.")); return false; }
    finally { setBusy(false); }
  }
  return { busy, error, success, run, clear: () => { setError(null); setSuccess(""); } };
}
