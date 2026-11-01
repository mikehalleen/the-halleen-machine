import { createContext, useContext, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

/**
 * The veil over a private project's pictures. The stylesheet does the hiding from a class on
 * the shell (`.is-veiled`, plus `.is-phone`): on a desktop a still shows only under the mouse
 * and a video shows nothing until it plays; on a phone every picture is hidden until the bar's
 * `reveal` pill is tapped, which lifts the veil for that page only -- the next navigation, or
 * tapping `hide`, drops it again. Components that would hand the browser a picture outside the
 * stylesheet's reach (a video's poster, its preloaded first frame) read `veiled` here.
 */
export interface Veil {
  /** The project is private: pictures are under the veil unless revealed. */
  veiled: boolean;
  /** The phone pill lifted the veil for this page. */
  revealed: boolean;
  toggle: () => void;
}

const NONE: Veil = { veiled: false, revealed: false, toggle: () => {} };
const VeilContext = createContext<Veil>(NONE);
export const VeilProvider = VeilContext.Provider;

export function useVeil(): Veil {
  return useContext(VeilContext);
}

/** The shell's veil state for `project`: revealed resets on every navigation. */
export function useVeilState(isPrivate: boolean): Veil {
  const { pathname } = useLocation();
  const [revealed, setRevealed] = useState(false);
  useEffect(() => setRevealed(false), [pathname]);
  return {
    veiled: isPrivate && !revealed,
    revealed: isPrivate && revealed,
    toggle: () => setRevealed((r) => !r),
  };
}
