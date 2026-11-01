import { useEffect, useRef } from "react";

/**
 * Files pasted from the clipboard (Ctrl+V / ⌘V): a screenshot, an image copied from a page or
 * another app, occasionally a clip. Every place that takes an upload can take a paste instead
 * of a browse, through one document listener.
 *
 * Takers stack in mount order and the top one answers first: an open upload sheet takes the
 * paste over the picker under it, the picker over the bin screen under that. A taker returns
 * `false` to pass (the paste was for something else, say the agent composer beside the screen)
 * and the next one down is asked. A paste that carries no media file is left alone entirely,
 * so text pastes into fields are untouched.
 */
type PasteTaker = (files: File[], e: ClipboardEvent) => boolean | void;

const takers: PasteTaker[] = [];

/** The media files on a paste event, with the clipboard's anonymous `image.png` given a name. */
export function clipboardFiles(e: ClipboardEvent): File[] {
  const out: File[] = [];
  for (const item of Array.from(e.clipboardData?.items ?? [])) {
    if (item.kind !== "file") continue;
    const file = item.getAsFile();
    if (!file || !(file.type.startsWith("image/") || file.type.startsWith("video/"))) continue;
    out.push(file.name && file.name !== "image.png" ? file : named(file));
  }
  return out;
}

function named(file: File): File {
  const ext = file.type.split("/")[1]?.replace("jpeg", "jpg") || "png";
  const t = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp = `${t.getFullYear()}${pad(t.getMonth() + 1)}${pad(t.getDate())}-${pad(t.getHours())}${pad(t.getMinutes())}${pad(t.getSeconds())}`;
  return new File([file], `pasted-${stamp}.${ext}`, { type: file.type });
}

function onPaste(e: ClipboardEvent) {
  const files = clipboardFiles(e);
  if (!files.length) return;
  for (let i = takers.length - 1; i >= 0; i--) {
    if (takers[i](files, e) !== false) {
      e.preventDefault();
      return;
    }
  }
}

/** Register as the taker for pasted media while `enabled`; the newest enabled taker is asked first. */
export function usePasteFiles(take: PasteTaker, enabled = true) {
  const latest = useRef(take);
  latest.current = take;
  useEffect(() => {
    if (!enabled) return;
    const taker: PasteTaker = (files, e) => latest.current(files, e);
    takers.push(taker);
    if (takers.length === 1) document.addEventListener("paste", onPaste);
    return () => {
      const at = takers.indexOf(taker);
      if (at >= 0) takers.splice(at, 1);
      if (!takers.length) document.removeEventListener("paste", onPaste);
    };
  }, [enabled]);
}

/** Whether a paste landed inside the agent panel, which has its own taker. */
export function pastedInAgentPanel(e: ClipboardEvent): boolean {
  const at = e.target as Element | null;
  // a card's reply area takes its own pastes
  return !!at?.closest?.(".agent-panel") && !at?.closest?.(".change-note-area");
}
