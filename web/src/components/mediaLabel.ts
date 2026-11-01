import type { BinDetail, Media } from "../api/types";

/** `take 3` for a take generated here; `#2` (its place in the gallery) for an upload or a
 * clip, or a take that came from another bin. The bin screen's tiles and the agent composer's
 * "about" pill read the same words. */
export function mediaLabel(bin: BinDetail["bin"], m: Media): string {
  if (m.take && m.origin.type === "generation" && m.origin.bin === bin.id) return `take ${m.take}`;
  return `#${(bin.media ?? []).indexOf(m.id) + 1}`;
}
