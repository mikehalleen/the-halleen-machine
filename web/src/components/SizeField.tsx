import { useSyncExternalStore } from "react";

/**
 * Quarter size: a per-generation draft toggle for video — half the project's width and height,
 * a quarter of the pixels, output kept at that size. The 2.0 form of 1.x's project-wide
 * `quarter_size_video`. It rides the generation as `params.quarter`; the video runner halves
 * the canvas, the image runner ignores it.
 *
 * The choice is session state, not project state: once on, it stays on for every video
 * generation in this tab, whichever project — and it is never written to a project or the
 * server. `sessionStorage` gives exactly that scope (survives a reload, dies with the tab).
 */

const KEY = "thm.quarter";
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

/** `[quarter, setQuarter]` — shared across every mounted user in the tab. */
export function useQuarterSize(): [boolean, (on: boolean) => void] {
  const on = useSyncExternalStore(subscribe, read, () => false);
  return [on, write];
}

/** The generation params the toggle contributes; spread into a generate body. */
export function quarterParams(on: boolean): Record<string, unknown> {
  return on ? { quarter: true } : {};
}

interface Props {
  /** The project canvas, for the tooltip's readout of what quarter size comes to. */
  width?: number | null;
  height?: number | null;
  disabled?: boolean;
}

/** The footer's ¼ toggle: on = the next video generations run at quarter size. Icon only;
 * the tooltip carries the words and the resulting canvas. */
export function QuarterToggle({ width, height, disabled }: Props) {
  const [quarter, setQuarter] = useQuarterSize();
  const dims = width && height ? ` (${Math.floor(width / 2)}×${Math.floor(height / 2)})` : "";
  return (
    <button
      type="button"
      className={`btn-outline btn-quarter ${quarter ? "is-on" : ""}`}
      aria-pressed={quarter}
      aria-label="quarter size"
      disabled={disabled}
      onClick={() => setQuarter(!quarter)}
      title={quarter ? `quarter size on${dims} — stays on for every video this session` : `quarter size: half the width and height${dims}, a quick draft`}
    >
      ¼
    </button>
  );
}
