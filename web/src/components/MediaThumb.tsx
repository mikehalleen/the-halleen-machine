import { useState, type KeyboardEvent, type MouseEvent } from "react";
import { thumbUrl } from "../api/client";
import type { MediaType } from "../api/types";
import { useMediaViewer } from "../layout/useMediaViewer";

interface Props {
  slug: string;
  id?: string | null;
  type?: MediaType | null;
  /** Seconds, when the caller has the media record; shown in the video marker. */
  duration?: number | null;
  /** The tile's side in px: its height, and its width too unless `ratio` says otherwise. */
  size?: number;
  /** Width over height of the media. Given, the tile takes the media's own shape at `size`
   * tall, clamped between 1:2 and 2:1 so one panorama cannot swallow a row. Omitted, the tile
   * is square. The rule: a tile that *is* the media shows its shape; a tile that stands for a
   * container of media (a project, a bin) is square. */
  ratio?: number | null;
  selected?: boolean;
  dim?: boolean;
  dashed?: boolean;
  /** A take that landed and nobody has looked at yet: a dot on the tile until it is shown. */
  fresh?: boolean;
  label?: string;
  /** Fill the box the caller lays out instead of taking a fixed square — the inline size would
   * otherwise beat the stylesheet and leave the tile wider than the image inside it. */
  fill?: boolean;
  /** Inert: no tap, no focus, no click swallowed — the tile is decoration inside whatever
   * link or button contains it, and the container's action is the only action. */
  passive?: boolean;
  /** What a tap does. Default: open the full-screen preview. The takes row passes select-this-take. */
  onClick?: () => void;
}

/** Width over height from a media record, or undefined when it has no dimensions yet. */
export function ratioOf(m: { width?: number | null; height?: number | null } | null | undefined): number | undefined {
  return m?.width && m?.height ? m.width / m.height : undefined;
}

/**
 * The video marker on a still: a small camera glyph and the length, in the corner, the way a
 * photo library marks its clips. Deliberately not a play button — tapping a thumbnail selects,
 * binds, or opens; nothing plays until the viewer. The big play control lives there and in the clipper.
 */
export function VideoBadge({ duration }: { duration?: number | null }) {
  const secs = duration != null && duration > 0 ? (duration >= 10 ? `${Math.round(duration)}s` : `${duration.toFixed(1)}s`) : "";
  return (
    <span className="badge-video" aria-label={secs ? `video, ${secs}` : "video"}>
      <svg viewBox="0 0 16 12" width="11" height="8" aria-hidden="true">
        <rect x="0.5" y="0.5" width="10" height="11" rx="1.5" fill="currentColor" />
        <path d="M11 4.2 15.5 1.5v9L11 7.8z" fill="currentColor" />
      </svg>
      {secs && <span>{secs}</span>}
    </span>
  );
}

/**
 * One thumbnail tile: grid webp, corner marker for video, dashed placeholder when empty or broken.
 * Every tile with media is tappable, even inside a card link: the tap is the tile's, not the card's.
 */
export function MediaThumb({ slug, id, type, duration, size = 56, ratio, selected, dim, dashed, fresh, label, fill, passive, onClick }: Props) {
  const [broken, setBroken] = useState(false);
  const viewer = useMediaViewer();
  const empty = !id || broken;
  const width = ratio ? Math.round(size * Math.min(2, Math.max(0.5, ratio))) : size;
  // fill + ratio: the box the caller lays out sets the width, and the height follows the media
  const style = fill
    ? { opacity: dim ? 0.45 : 1, ...(ratio ? { width: "100%", height: "auto", aspectRatio: String(Math.min(2, Math.max(0.5, ratio))) } : {}) }
    : { width, height: size, opacity: dim ? 0.45 : 1 };
  const act = (e: MouseEvent | KeyboardEvent) => {
    if (empty || passive) return;
    e.preventDefault();
    e.stopPropagation();
    if (onClick) onClick();
    else viewer.open(slug, id!);
  };
  return (
    <div
      className={`thumb ${selected ? "is-selected" : ""} ${empty || dashed ? "is-empty" : ""} ${empty || passive ? "" : "is-tappable"}`}
      style={style}
      title={label}
      role={empty || passive ? undefined : "button"}
      tabIndex={empty || passive ? undefined : 0}
      onClick={passive ? undefined : act}
      onKeyDown={passive ? undefined : (e) => {
        if (e.key === "Enter" || e.key === " ") act(e);
      }}
    >
      {!empty && <img src={thumbUrl(slug, id!, "grid")} alt={label ?? ""} loading="lazy" onError={() => setBroken(true)} />}
      {!empty && type === "video" && <VideoBadge duration={duration} />}
      {!empty && fresh && <span className="thumb-new" role="img" aria-label="new, not yet viewed" title="new — not yet viewed" />}
    </div>
  );
}
