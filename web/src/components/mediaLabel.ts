import type { BinDetail, Media } from "../api/types";

/** `take 3` for a take generated here; `file 2` (its place in the gallery) for an upload or a
 * clip, or a take that came from another bin. The bin screen's tiles and the agent composer's
 * "about" pill read the same words. */
export function mediaLabel(bin: BinDetail["bin"], m: Media): string {
  if (m.take && m.origin.type === "generation" && m.origin.bin === bin.id) return `take ${m.take}`;
  return `file ${(bin.media ?? []).indexOf(m.id) + 1}`;
}

/** Seconds as a clock: `0:42`, `1:23`, `1:02:03`. Hours appear only past the first one. */
export function clockTime(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** How long ComfyUI spent rendering a generated take, as a clock, or null when the take has no
 * such figure: an upload, a clip, or a render from before the timing was kept. */
export function genTime(m: Media): string | null {
  const s = m.origin.type === "generation" ? m.origin.exec_seconds : null;
  return s != null && Number.isFinite(s) ? clockTime(s) : null;
}
