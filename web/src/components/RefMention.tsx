import { useEffect, useRef, type ReactNode } from "react";
import type { MediaType } from "../api/types";
import { MediaThumb } from "./MediaThumb";

/** Where an `@` token is being typed: the span it occupies and what has been typed after the `@`. */
export interface Mention {
  start: number;
  end: number;
  query: string;
}

/** What one option in the menu shows. */
export interface MentionOption {
  handle: string;
  mediaId?: string | null;
  mediaType?: MediaType | null;
  /** The fixed slot label the tile wears: `image 2`, `start frame`, `unused`, `missing`. */
  slot: string;
  dim?: boolean;
}

// the run a handle can be spelled with: slug, dot, extension, optional dot and counter
const TOKEN_CHARS = /^[a-z0-9.-]*$/i;

/**
 * The `@` token under the caret, or null when the caret is not on one. A token starts at an
 * `@` that opens a word (start of text, after whitespace or an opening bracket or quote) and
 * runs through handle characters; `query` is what sits between the `@` and the caret, `end`
 * reaches past the caret to the token's end so a pick replaces the whole word.
 */
export function findMention(value: string, caret: number): Mention | null {
  const before = value.slice(0, caret);
  const at = before.lastIndexOf("@");
  if (at < 0) return null;
  const run = before.slice(at + 1);
  if (!TOKEN_CHARS.test(run)) return null;
  if (at > 0 && !/[\s([{"'“‘]/.test(before[at - 1])) return null;
  const after = value.slice(caret).match(/^[a-z0-9.-]*/i)?.[0] ?? "";
  return { start: at, end: caret + after.length, query: run.toLowerCase() };
}

/** The options that answer to what has been typed: prefix matches first, then anywhere. */
export function matchMentions(options: MentionOption[], query: string): MentionOption[] {
  if (!query) return options;
  const starts = options.filter((o) => o.handle.startsWith(query));
  const within = options.filter((o) => !o.handle.startsWith(query) && o.handle.includes(query));
  return [...starts, ...within];
}

/**
 * Write `@handle ` over the token span in a textarea and report the new value. The trailing
 * space is what closes the menu: the caret is no longer on an `@` word. Focus returns to the
 * box so typing carries on.
 */
export function insertMention(el: HTMLTextAreaElement, mention: Mention, handle: string): string {
  el.setRangeText(`@${handle} `, mention.start, mention.end, "end");
  el.focus();
  return el.value;
}

interface Props {
  slug: string;
  options: MentionOption[];
  highlight: number;
  onPick: (handle: string) => void;
  onHover: (index: number) => void;
  /** The elements a tap may land on without closing the menu (the prompt box, the reference tiles). */
  keepOpenWithin: React.RefObject<HTMLElement>[];
  onDismiss: () => void;
  children?: ReactNode;
}

/**
 * The menu under the prompt while an `@` word is being typed: one chip per bound reference
 * that matches, thumbnail, `@handle` and its slot. Tapping a chip writes the handle; the
 * reference tiles below are live targets too while this shows (the screen marks them).
 * In flow rather than floating at the caret: a textarea cannot say where its caret is, and a
 * phone keyboard sits right under here anyway.
 */
export function RefMention({ slug, options, highlight, onPick, onHover, keepOpenWithin, onDismiss }: Props) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (e: MouseEvent | TouchEvent) => {
      const t = e.target as Node;
      if (box.current?.contains(t)) return;
      if (keepOpenWithin.some((r) => r.current?.contains(t))) return;
      onDismiss();
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("touchstart", away);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("touchstart", away);
    };
  }, [keepOpenWithin, onDismiss]);

  return (
    <div ref={box} className="ref-mention" role="listbox" aria-label="Bound references">
      {options.length === 0 && <span className="ref-mention-empty">no reference matches — keep typing, Delete to clear, or tap a tile below</span>}
      {options.map((o, i) => (
        <button
          key={o.handle}
          type="button"
          role="option"
          aria-selected={i === highlight}
          className={`ref-mention-option ${i === highlight ? "is-on" : ""} ${o.dim ? "is-dim" : ""}`}
          // keep the caret in the prompt: the pick writes there, and a blur would drop the keyboard
          onMouseDown={(e) => e.preventDefault()}
          onMouseEnter={() => onHover(i)}
          onClick={() => onPick(o.handle)}
          title={o.slot}
        >
          <MediaThumb slug={slug} id={o.mediaId} type={o.mediaType} size={24} passive />
          <span className="ref-mention-handle">@{o.handle}</span>
          <span className="ref-mention-slot">{o.slot}</span>
        </button>
      ))}
      <span className="ref-mention-keys" aria-hidden="true">
        ↑↓ or Tab move · Enter picks · Delete clears
      </span>
    </div>
  );
}
