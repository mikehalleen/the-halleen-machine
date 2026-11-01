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
      aria-label="save for later"
      disabled={disabled}
      onClick={() => setLater(!later)}
      title={
        later
          ? "save for later is on — Generate snapshots the recipe as it stands; start the saved group from the bar when you're done"
          : "save for later: snapshot this recipe now, render it when you start the saved group"
      }
    >
      {LATER_GLYPH}
    </button>
  );
}
