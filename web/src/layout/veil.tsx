import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { VIEW_PARAM } from "./useMediaViewer";

/**
 * The veil over a private project's pictures. The stylesheet does the hiding from a class on
 * the shell (`.is-veiled`, plus `.is-phone`): on a desktop a still shows only under the mouse
 * and a video shows nothing until it plays; on a phone every picture is hidden until the bar's
 * lock pill is tapped, which lifts the veil for that page only. It drops again on any
 * navigation (another screen, the gallery ↔ generate switch), on any press of Generate, when
 * `hide` is tapped, when the window loses focus, and after `IDLE_LOCK_MS` with no touch, key,
 * scroll or playback since the unlock. Components that would hand the browser a picture outside the stylesheet's reach (a
 * video's poster, its preloaded first frame) read `veiled` here.
 */
export interface Veil {
  /** The project is private: pictures are under the veil unless revealed. */
  veiled: boolean;
  /** The phone pill lifted the veil for this page. */
  revealed: boolean;
  toggle: () => void;
}

/** How long an unlocked page stays unlocked with nobody touching it. */
export const IDLE_LOCK_MS = 20_000;
const LOCK_EVENT = "thm:veil-lock";
// what counts as the user still being here: any of these resets the idle clock
const ACTIVITY_EVENTS = ["pointerdown", "keydown", "scroll", "touchmove", "play", "timeupdate"];

const NONE: Veil = { veiled: false, revealed: false, toggle: () => {} };
const VeilContext = createContext<Veil>(NONE);
export const VeilProvider = VeilContext.Provider;

export function useVeil(): Veil {
  return useContext(VeilContext);
}

/** Drop the veil from anywhere -- a Generate press, say -- without a handle on the shell's
 * state. A no-op when nothing is revealed. */
export function lockVeil(): void {
  window.dispatchEvent(new Event(LOCK_EVENT));
}

/** The shell's veil state for `project`: revealed resets on every navigation, on `lockVeil()`,
 * and after `IDLE_LOCK_MS` of no activity. */
export function useVeilState(isPrivate: boolean): Veil {
  const { pathname, search } = useLocation();
  // the gallery ↔ generate switch is a navigation, and so is opening or leaving the full-screen
  // viewer: what was revealed for one of them is not revealed for the next
  const params = new URLSearchParams(search);
  const generating = params.has("generate");
  const viewing = params.has(VIEW_PARAM);
  const [revealed, setRevealed] = useState(false);
  const lock = useCallback(() => setRevealed(false), []);
  useEffect(lock, [pathname, generating, viewing, lock]);
  useEffect(() => {
    window.addEventListener(LOCK_EVENT, lock);
    return () => window.removeEventListener(LOCK_EVENT, lock);
  }, [lock]);
  // a window that is no longer in front is a window someone else may be looking at. These go
  // through the event, not the setter, so a playing video hears the veil drop too (useOnVeilDrop)
  useEffect(() => {
    if (!revealed) return;
    const onHidden = () => document.visibilityState === "hidden" && lockVeil();
    window.addEventListener("blur", lockVeil);
    document.addEventListener("visibilitychange", onHidden);
    return () => {
      window.removeEventListener("blur", lockVeil);
      document.removeEventListener("visibilitychange", onHidden);
    };
  }, [revealed]);
  useEffect(() => {
    if (!revealed) return;
    let timer = 0;
    const arm = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(lockVeil, IDLE_LOCK_MS);
    };
    // capture: media and scroll events do not bubble
    for (const e of ACTIVITY_EVENTS) document.addEventListener(e, arm, true);
    arm();
    return () => {
      window.clearTimeout(timer);
      for (const e of ACTIVITY_EVENTS) document.removeEventListener(e, arm, true);
    };
  }, [revealed, lock]);
  return {
    veiled: isPrivate && !revealed,
    revealed: isPrivate && revealed,
    // locking is the event: it also hides every click-revealed picture and stops every player
    toggle: () => (revealed ? lockVeil() : setRevealed(true)),
  };
}

/**
 * On a phone there is no mouse to leave with: a tap anywhere outside ``ref`` while ``active``
 * runs ``fn`` (the player puts itself back under the veil). Capture phase, so a tap that another
 * handler swallows still counts; a tap inside the element is the player's own business.
 */
export function useTapOutside(ref: React.RefObject<HTMLElement | null>, active: boolean, fn: () => void): void {
  const cb = useRef(fn);
  cb.current = fn;
  useEffect(() => {
    if (!active) return;
    const on = (e: PointerEvent) => {
      const el = ref.current;
      if (el && !el.contains(e.target as Node)) cb.current();
    };
    document.addEventListener("pointerdown", on, true);
    return () => document.removeEventListener("pointerdown", on, true);
  }, [ref, active]);
}

/**
 * Run ``fn`` whenever the veil drops: the bar's padlock, a Generate press, the window losing
 * focus, the idle lock. The stylesheet alone cannot stop a playing video (one that plays is
 * shown on purpose), so a player pauses, hides and mutes itself from here.
 */
export function useOnVeilDrop(fn: () => void): void {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const on = () => ref.current();
    window.addEventListener(LOCK_EVENT, on);
    return () => window.removeEventListener(LOCK_EVENT, on);
  }, []);
}

/**
 * The large hero picture of a private project wants a click to show, not a hover: a passer-by's
 * mouse must not uncover it. `[shown, reveal]` for one picture, keyed by what it shows: a new
 * picture starts hidden again, and so does everything that locks the veil (a Generate press,
 * the window losing focus, `IDLE_LOCK_MS` with nobody touching anything). The caller hides it
 * again when the mouse leaves the picture.
 */
export function useClickReveal(key: string): [boolean, () => void, () => void] {
  const [shownKey, setShownKey] = useState<string | null>(null);
  const shown = shownKey === key;
  const hide = useCallback(() => setShownKey(null), []);
  useEffect(() => {
    window.addEventListener(LOCK_EVENT, hide);
    return () => window.removeEventListener(LOCK_EVENT, hide);
  }, [hide]);
  useEffect(() => {
    if (!shown) return;
    const onHidden = () => document.visibilityState === "hidden" && hide();
    let timer = 0;
    const arm = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(hide, IDLE_LOCK_MS);
    };
    window.addEventListener("blur", hide);
    document.addEventListener("visibilitychange", onHidden);
    for (const e of ACTIVITY_EVENTS) document.addEventListener(e, arm, true);
    arm();
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("blur", hide);
      document.removeEventListener("visibilitychange", onHidden);
      for (const e of ACTIVITY_EVENTS) document.removeEventListener(e, arm, true);
    };
  }, [shown, hide]);
  return [shown, () => setShownKey(key), hide];
}
