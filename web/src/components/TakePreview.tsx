import { useState, type CSSProperties } from "react";
import { mediaUrl, thumbUrl } from "../api/client";
import type { Media } from "../api/types";
import { useMediaViewer } from "../layout/useMediaViewer";
import { useVeil } from "../layout/veil";

interface Props {
  slug: string;
  media: Media;
  badge?: string;
  /** The video started playing: the first time it is really looked at, not merely selected. */
  onPlayed?: () => void;
}

/**
 * The large current-take preview: detail webp for images (tap opens the full-size viewer),
 * a native player with Range for video.
 */
export function TakePreview({ slug, media, badge, onPlayed }: Props) {
  const viewer = useMediaViewer();
  // under the veil a video gives nothing away until it plays: no poster, no preloaded first frame
  const { veiled } = useVeil();
  // .is-playing keeps a veiled video visible while it plays; paused and not hovered, it hides again
  const [playing, setPlaying] = useState(false);
  // width ÷ height as a bare number: .preview sizes the frame from it, so a square or portrait
  // take gets a frame its own shape instead of a wide one with black bars filling the slack.
  const ar = media.width && media.height ? media.width / media.height : 16 / 9;
  return (
    <div className={`preview ${playing ? "is-playing" : ""}`} style={{ "--ar": ar } as CSSProperties}>
      {media.type === "video" ? (
        <video
          controls
          playsInline
          preload={veiled ? "none" : "metadata"}
          poster={veiled ? undefined : thumbUrl(slug, media.id, "poster")}
          src={mediaUrl(slug, media.id)}
          onPlay={() => {
            setPlaying(true);
            onPlayed?.();
          }}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
        />
      ) : (
        <img
          src={thumbUrl(slug, media.id, "detail")}
          alt=""
          className="is-tappable"
          role="button"
          tabIndex={0}
          title="open full size"
          onClick={() => viewer.open(slug, media.id)}
          onKeyDown={(e) => {
            if (e.key === "Enter") viewer.open(slug, media.id);
          }}
        />
      )}
      {badge && <span className="preview-badge">{badge}</span>}
    </div>
  );
}
