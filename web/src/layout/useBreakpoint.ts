import { useEffect, useState } from "react";
import { BP_WIDE, BP_XWIDE } from "./breakpoints";

export interface Breakpoint {
  /** >= 760: sidebar, two-pane shot, slide-over library */
  wide: boolean;
  /** >= 1120: agent rail slot */
  xwide: boolean;
}

function read(): Breakpoint {
  if (typeof window === "undefined") return { wide: true, xwide: false };
  return {
    wide: window.matchMedia(`(min-width: ${BP_WIDE}px)`).matches,
    xwide: window.matchMedia(`(min-width: ${BP_XWIDE}px)`).matches,
  };
}

export function useBreakpoint(): Breakpoint {
  const [bp, setBp] = useState<Breakpoint>(read);
  useEffect(() => {
    const wideQ = window.matchMedia(`(min-width: ${BP_WIDE}px)`);
    const xwideQ = window.matchMedia(`(min-width: ${BP_XWIDE}px)`);
    const update = () => setBp({ wide: wideQ.matches, xwide: xwideQ.matches });
    wideQ.addEventListener("change", update);
    xwideQ.addEventListener("change", update);
    update();
    return () => {
      wideQ.removeEventListener("change", update);
      xwideQ.removeEventListener("change", update);
    };
  }, []);
  return bp;
}
