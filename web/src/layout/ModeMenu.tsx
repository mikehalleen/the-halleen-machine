import { useEffect, useRef, useState } from "react";
import type { SessionMode } from "../api/types";
import { DRAFTS_HINT, MODES, modeLabel } from "./modes";

interface Props {
  mode: SessionMode;
  onChange: (mode: SessionMode) => void;
  /** On a phone the draft-mode switch rides in the menu, under the modes, to save header room. */
  drafts?: { on: boolean; onChange: (on: boolean) => void };
  disabled?: boolean;
  /** The panel header's slightly larger padding. */
  header?: boolean;
}

/**
 * The mode pill: `auto ⌄`, a control wherever it appears (the bar's tenant line, the panel
 * header), since mode changes often. Opens a small fixed-position list, so the bar's
 * overflow:hidden cannot clip it.
 */
export function ModeMenu({ mode, onChange, drafts, disabled, header }: Props) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number }>({ top: 0, right: 0 });
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  const show = () => {
    const r = button.current?.getBoundingClientRect();
    if (r) setPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent | TouchEvent) => {
      const t = e.target as Node;
      if (menu.current?.contains(t) || button.current?.contains(t)) return;
      setOpen(false);
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const close = () => setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("touchstart", away);
    window.addEventListener("keydown", key);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("touchstart", away);
      window.removeEventListener("keydown", key);
      window.removeEventListener("resize", close);
    };
  }, [open]);

  return (
    <>
      <button
        ref={button}
        type="button"
        className={`mode-pill ${header ? "is-header" : ""}`}
        onClick={() => (open ? setOpen(false) : show())}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="how much the agent asks"
      >
        <span className="mode-pill-text">
          {modeLabel(mode)}
          {drafts?.on ? " · draft" : ""}
        </span>{" "}
        <span aria-hidden="true">⌄</span>
      </button>
      {open && (
        <div ref={menu} className="mode-menu" role="listbox" aria-label="Session mode" style={{ top: pos.top, right: pos.right }}>
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              role="option"
              aria-selected={m.value === mode}
              className={`mode-option ${m.value === mode ? "is-on" : ""}`}
              onClick={() => {
                setOpen(false);
                if (m.value !== mode) onChange(m.value);
              }}
            >
              <span className="mode-option-check" aria-hidden="true">
                {m.value === mode ? "✓" : ""}
              </span>
              <span title={m.hint}>{m.label}</span>
            </button>
          ))}
          {drafts && (
            <label className="mode-drafts" title={DRAFTS_HINT}>
              <input type="checkbox" checked={drafts.on} onChange={(e) => drafts.onChange(e.target.checked)} />
              draft mode
            </label>
          )}
        </div>
      )}
    </>
  );
}
