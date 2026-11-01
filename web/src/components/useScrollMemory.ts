import { useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router-dom";

/**
 * Remember a scrolling element's position per history entry, so back lands where the
 * user left a list. BrowserRouter restores nothing on its own. `ready` re-applies the
 * saved position once the list's data has arrived.
 */
export function useScrollMemory<T extends HTMLElement>(ready: boolean) {
  const location = useLocation();
  const ref = useRef<T>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const key = `thm.scroll.${location.key}`;
    try {
      const saved = sessionStorage.getItem(key);
      if (saved) el.scrollTop = Number(saved);
    } catch {
      /* storage unavailable: start at the top */
    }
    const save = () => {
      try {
        sessionStorage.setItem(key, String(el.scrollTop));
      } catch {
        /* ignore */
      }
    };
    el.addEventListener("scroll", save, { passive: true });
    return () => {
      save();
      el.removeEventListener("scroll", save);
    };
  }, [location.key, ready]);
  return ref;
}
