import type { SessionMode } from "../api/types";

/** How much the agent asks, least to most autonomous. Full auto is the default: state the goal, the
 * agent loops, one card when it thinks it is done. Draft mode is a separate switch on the session,
 * not a mode in this list: it rides with any of these. */
export const MODES: { value: SessionMode; label: string; hint: string }[] = [
  { value: "plan", label: "plan", hint: "revises the brief; nothing is generated until you pick another mode" },
  { value: "manual", label: "manual", hint: "proposes every generation as a card first; approving starts it" },
  { value: "image_review", label: "media cards", hint: "generates on its own; every take is a card, and it waits for you" },
  { value: "video_review", label: "video cards", hint: "checks the stills itself; every video take is a card, and it waits" },
  { value: "auto", label: "full auto", hint: "loops on the goal; the finished candidate is the card" },
];

export const DRAFTS_HINT = "videos at 1/4 size and workflows marked draft in settings run freely; a full-quality generation asks first — yes, later or no";

export function modeLabel(mode: SessionMode | null | undefined): string {
  return MODES.find((m) => m.value === mode)?.label ?? "full auto";
}

const KEY = "thm.lastMode";
const DRAFTS_KEY = "thm.lastDrafts";

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

/** The draft-mode switch a one-click start uses, remembered the same way. */
export function readLastDrafts(fallback?: boolean | null): boolean {
  try {
    const v = window.localStorage.getItem(DRAFTS_KEY);
    if (v === "1" || v === "0") return v === "1";
  } catch {
    /* private mode: fall through */
  }
  return !!fallback;
}

export function writeLastDrafts(drafts: boolean) {
  try {
    window.localStorage.setItem(DRAFTS_KEY, drafts ? "1" : "0");
  } catch {
    /* private mode */
  }
}
