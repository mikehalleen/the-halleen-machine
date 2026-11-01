import type { SessionMode } from "../api/types";

/** How much the agent asks. Auto is the default: state the goal, the agent loops, one card when it thinks it is done. */
export const MODES: { value: SessionMode; label: string; hint: string }[] = [
  { value: "auto", label: "auto", hint: "loops on the goal; the finished candidate is the card" },
  { value: "manual", label: "fully manual", hint: "proposes every render as a card first; approving starts it" },
  { value: "image_review", label: "image review", hint: "renders on its own; every take is a card, and it waits for you" },
  { value: "video_review", label: "video review", hint: "checks the stills itself; every video take is a card, and it waits" },
  { value: "plan", label: "plan", hint: "revises the brief; approving it starts auto" },
];

export function modeLabel(mode: SessionMode | null | undefined): string {
  return MODES.find((m) => m.value === mode)?.label ?? "auto";
}

const KEY = "thm.lastMode";

/** The mode a one-click start uses: the last one chosen on this device, else the project's
 * last session's, else auto. */
export function readLastMode(fallback?: SessionMode | null): SessionMode {
  try {
    const v = window.localStorage.getItem(KEY);
    if (v && MODES.some((m) => m.value === v)) return v as SessionMode;
  } catch {
    /* private mode: fall through */
  }
  return fallback && MODES.some((m) => m.value === fallback) ? fallback : "auto";
}

export function writeLastMode(mode: SessionMode) {
  try {
    window.localStorage.setItem(KEY, mode);
  } catch {
    /* private mode: the next start reads the project's session instead */
  }
}
