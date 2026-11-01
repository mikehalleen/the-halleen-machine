import { useSyncExternalStore } from "react";

/**
 * Draft: the workflow's own cheap setting, for a workflow that has one (a `THM-IsDraft` switch
 * in its graph: fewer steps, a distilled model...). It rides the generation as `params.draft`;
 * the runner flips the switch to its draft branch. Separate from ¼ (quarter size): by hand the
 * two are independent; the agent's draft mode turns on both.
 *
 * Session state like ¼: once on, it stays on for every render on a workflow that supports it in
 * this tab, whichever project, and is never written to a project or the server.
 */

const KEY = "thm.draft";
const listeners = new Set<() => void>();

function read(): boolean {
  try {
    return sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

function write(on: boolean) {
  try {
    if (on) sessionStorage.setItem(KEY, "1");
    else sessionStorage.removeItem(KEY);
  } catch {
    /* private mode etc.: the toggle then lasts until the next navigation */
  }
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

/** `[draft, setDraft]` — shared across every mounted user in the tab. */
export function useDraft(): [boolean, (on: boolean) => void] {
  const on = useSyncExternalStore(subscribe, read, () => false);
  return [on, write];
}

/** The generation params the toggle contributes, only where the workflow has a draft setting. */
export function draftParams(on: boolean, supported: boolean): Record<string, unknown> {
  return on && supported ? { draft: true } : {};
}

/** The footer's Draft toggle, shown only for a workflow with a THM-IsDraft switch. */
export function DraftToggle({ disabled }: { disabled?: boolean }) {
  const [draft, setDraft] = useDraft();
  return (
    <button
      type="button"
      className={`btn-outline btn-quarter btn-draft ${draft ? "is-on" : ""}`}
      aria-pressed={draft}
      disabled={disabled}
      onClick={() => setDraft(!draft)}
      title={draft ? "draft on — this workflow's draft setting, for every generation this session that has one" : "draft: use this workflow's cheaper draft setting"}
    >
      Draft
    </button>
  );
}
