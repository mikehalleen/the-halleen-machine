import { useSyncExternalStore } from "react";

/**
 * Save for later: a per-generation toggle next to ¼. On, the Generate button saves the recipe
 * exactly as it stands (prompt, references, workflow, params resolved on the server) as a held
 * generation instead of rendering it. Edits after the save never reach it. The project's held
 * group is released from the system bar (`◷ n saved · start →`), and each one then goes to
 * ComfyUI through the ordinary queue.
 *
 * Session state like the quarter toggle (`SizeField.tsx`): once on it stays on for every
 * generation in this tab until turned off, never written to a project or the server.
 */

const KEY = "thm.later";
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
    /* private mode etc.: the toggle then lasts until the next navigation, which is still a session */
  }
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

/** `[later, setLater]` — shared across every mounted user in the tab. */
export function useLater(): [boolean, (on: boolean) => void] {
  const on = useSyncExternalStore(subscribe, read, () => false);
  return [on, write];
}

/** The glyph the held state wears everywhere: the toggle, the strip tile, the bar pill. */
export const LATER_GLYPH = "◷";

/** The toggle's clock, drawn: the ◷ character falls back to a tiny glyph in phone fonts. */
function LaterIcon() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M10 5.2V10l3.2 2.2" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

interface Props {
  disabled?: boolean;
}

/** The footer's ◷ toggle: on = Generate saves the recipe for later instead of rendering it. */
export function LaterToggle({ disabled }: Props) {
  const [later, setLater] = useLater();
  return (
    <button
      type="button"
      className={`btn-outline btn-later ${later ? "is-on" : ""}`}
      aria-pressed={later}
      aria-label="generate later"
      disabled={disabled}
      onClick={() => setLater(!later)}
      title={
        later
          ? "generate later is on — Generate snapshots the recipe as it stands; start the waiting group from the bar when you're done"
          : "generate later: snapshot this recipe now, generate it when you start the waiting group"
      }
    >
      <LaterIcon />
    </button>
  );
}
