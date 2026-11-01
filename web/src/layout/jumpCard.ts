import { useEffect, useRef } from "react";

/**
 * "Take me to that card." A screen (the bin header, the activity row) asks; the feed, when it
 * is mounted, scrolls. The ask is kept in sessionStorage as well as sent as an event, so a
 * feed that mounts a moment later (the phone's panel push opening) still answers it.
 */
const EVENT = "thm:jump-card";
const KEY = "thm.jumpCard";

export function requestJump(changeId: string): void {
  try {
    sessionStorage.setItem(KEY, changeId);
  } catch {
    /* private mode: the event alone reaches a mounted feed */
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: changeId }));
}

function take(): string | null {
  try {
    const id = sessionStorage.getItem(KEY);
    if (id) sessionStorage.removeItem(KEY);
    return id;
  } catch {
    return null;
  }
}

/** The feed's side: `jump` runs for every request, and once on mount for one left waiting. */
export function useJumpRequests(jump: (changeId: string) => void, ready: boolean): void {
  const latest = useRef(jump);
  latest.current = jump;
  useEffect(() => {
    if (!ready) return;
    const pending = take();
    if (pending) latest.current(pending);
    const onEvent = (e: Event) => {
      take();
      latest.current(String((e as CustomEvent).detail));
    };
    window.addEventListener(EVENT, onEvent);
    return () => window.removeEventListener(EVENT, onEvent);
  }, [ready]);
}
