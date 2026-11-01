import { useCallback, useState, type CSSProperties } from "react";

const KEY = "thm.paneWidth";
const MIN = { sidebar: 200, rail: 340 } as const;
// neither column may leave the canvas less than this
const CANVAS_MIN = 420;

export type Pane = keyof typeof MIN;

function read(pane: Pane): number | null {
  try {
    const v = Number(localStorage.getItem(`${KEY}.${pane}`));
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}

function write(pane: Pane, px: number | null) {
  try {
    if (px === null) localStorage.removeItem(`${KEY}.${pane}`);
    else localStorage.setItem(`${KEY}.${pane}`, String(Math.round(px)));
  } catch {
    // a remembered width is a convenience
  }
}

/** The widths the user dragged the sidebar and the agent rail to, remembered on this device; a
 * column never dragged keeps its default from tokens.css. `style` sets them on the shell body. */
export function usePaneWidths() {
  const [widths, setWidths] = useState<Record<Pane, number | null>>(() => ({ sidebar: read("sidebar"), rail: read("rail") }));
  const set = useCallback((pane: Pane, px: number | null) => {
    setWidths((w) => ({ ...w, [pane]: px }));
    write(pane, px);
  }, []);
  const style = {
    ...(widths.sidebar ? { "--w-sidebar": `${widths.sidebar}px` } : {}),
    ...(widths.rail ? { "--w-rail": `${widths.rail}px` } : {}),
  } as CSSProperties;
  return { style, set };
}

/**
 * The drag edge of a side column: the sidebar's right edge, the rail's left. Drag to set the
 * width (kept on this device), double-click to go back to the default.
 */
export function PaneResizer({ pane, onWidth }: { pane: Pane; onWidth: (pane: Pane, px: number | null) => void }) {
  const start = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    const column = pane === "sidebar" ? handle.previousElementSibling : handle.nextElementSibling;
    const from = column?.getBoundingClientRect().width ?? 0;
    const x0 = e.clientX;
    const other = document.querySelector<HTMLElement>(pane === "sidebar" ? ".rail.is-open" : ".sidebar")?.getBoundingClientRect().width ?? 0;
    const max = Math.max(MIN[pane], window.innerWidth - other - CANVAS_MIN);
    document.body.classList.add("is-resizing-pane");
    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - x0;
      onWidth(pane, Math.min(max, Math.max(MIN[pane], from + (pane === "sidebar" ? dx : -dx))));
    };
    const end = () => {
      document.body.classList.remove("is-resizing-pane");
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  };
  return (
    <div
      className={`pane-resizer is-${pane}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={pane === "sidebar" ? "Resize the sidebar" : "Resize the agent panel"}
      title="drag to resize · double-click to reset"
      onPointerDown={start}
      onDoubleClick={() => onWidth(pane, null)}
    />
  );
}
