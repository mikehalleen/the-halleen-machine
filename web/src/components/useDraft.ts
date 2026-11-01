import { useCallback, useEffect, useState } from "react";

const PREFIX = "thm.draft.";

function read<T>(key: string, initial: T): T {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw === null ? initial : (JSON.parse(raw) as T);
  } catch {
    return initial; // private mode, blocked storage, or a stale shape: start clean
  }
}

function write<T>(key: string, value: T, initial: T): void {
  try {
    if (JSON.stringify(value) === JSON.stringify(initial)) localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    // nothing to do: the draft lives in state for as long as the component does
  }
}

/**
 * A draft that outlives the component: a half-written note on a card, the composer's text,
 * the toggles on a shots card. `Shell` remounts on every route change and a phone drops the
 * tab in the background, so plain state loses what the user typed; this keeps it in
 * localStorage under `thm.draft.<key>` (key it by slug and change id) until it equals
 * `initial` again -- sending or deciding clears it by setting it back.
 */
export function useDraft<T>(key: string, initial: T): [T, (next: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => read(key, initial));
  useEffect(() => {
    write(key, value, initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, value]);
  const set = useCallback((next: T | ((prev: T) => T)) => setValue((prev) => (typeof next === "function" ? (next as (p: T) => T)(prev) : next)), []);
  return [value, set];
}
