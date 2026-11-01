import { useRef, useState, type CSSProperties } from "react";
import { isGif, mediaUrl, thumbUrl } from "../api/client";
import type { Media } from "../api/types";
import { useMediaViewer } from "../layout/useMediaViewer";
import { useClickReveal, useOnVeilDrop, useTapOutside, useVeil } from "../layout/veil";
import { AudioPlayer } from "./AudioPlayer";

/** The wide frame an audio take gets: a waveform strip, not a picture's shape. */
const AUDIO_RATIO = 4;

interface Props {
  slug: string;
  media: Media;
  badge?: string;
  /** The video started playing: the first time it is really looked at, not merely selected. */
  onPlayed?: () => void;
}

/**
 * The large current-take preview: detail webp for images, the file itself for a GIF so it
 * plays (tap opens the full-size viewer),
 * a native player with Range for video.
 */
export function TakePreview({ slug, media, badge, onPlayed }: Props) {
  const viewer = useMediaViewer();
  // under the veil a video gives nothing away until it plays: no poster, no preloaded first frame
  const { veiled, revealed } = useVeil();
  const isPrivate = veiled || revealed; // sound is a per-project default, not a per-veil one
  // .is-playing keeps a veiled video visible while it plays; paused and not hovered, it hides again
  const [playing, setPlaying] = useState(false);
  // a private hero is hidden until clicked (hover shows nothing here, unlike the small tiles)
  // and hides again when the mouse leaves it (a video also pauses). For a still, the click that shows it does not also
  // open the viewer, the next one does; for a video, a cover takes the first click (so it shows
  // the frame rather than starting playback), and a playing video stays visible as before
  const [shown, reveal, conceal] = useClickReveal(media.id);
  const hidden = veiled && !shown && !(media.type === "video" && playing);
  // leaving a private hero stops and hides it: a video pauses, so nothing keeps playing unwatched
  const videoRef = useRef<HTMLVideoElement>(null);
  const leave = () => {
    videoRef.current?.pause();
    conceal();
  };
  // the veil dropping (the bar's padlock, idle, blur) pauses and silences a playing hero too:
  // the stylesheet keeps a playing video visible on purpose, so the player has to stop itself
  const drop = () => {
    if (!isPrivate) return;
    const v = videoRef.current;
    if (v) {
      v.pause();
      v.muted = true;
    }
    conceal();
  };
  useOnVeilDrop(drop);
  // a phone has no mouse to leave with: a tap outside the hero puts it back under the veil
  const frameRef = useRef<HTMLDivElement>(null);
  useTapOutside(frameRef, veiled && (shown || playing), drop);
  const open = () => (hidden ? reveal() : viewer.open(slug, media.id));
  // width ÷ height as a bare number: .preview sizes the frame from it, so a square or portrait
  // take gets a frame its own shape instead of a wide one with black bars filling the slack.
  const ar = media.type === "audio" ? AUDIO_RATIO : media.width && media.height ? media.width / media.height : 16 / 9;
  if (media.type === "audio") {
    // sound with no picture: the player takes the frame; its waveform is the one picture, and
    // under the veil it stays out until the take is revealed or plays
    return (
      <div ref={frameRef} className={`preview is-audio ${playing ? "is-playing" : ""} ${shown ? "is-revealed" : ""}`} onMouseLeave={veiled ? leave : undefined} style={{ "--ar": ar } as CSSProperties}>
        <AudioPlayer
          src={mediaUrl(slug, media.id)}
          waveform={hidden ? null : thumbUrl(slug, media.id, "detail")}
          duration={media.duration_s}
          muted={isPrivate}
          onPlay={() => {
            setPlaying(true);
            onPlayed?.();
          }}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
        />
        {/* a picture opens the viewer on a tap; the player's taps are its own, so the viewer gets a button */}
        <button type="button" className="preview-open" onClick={() => (hidden ? reveal() : viewer.open(slug, media.id))} title="open in the viewer" aria-label="Open in the viewer">
          ⤢
        </button>
        {badge && <span className="preview-badge">{badge}</span>}
      </div>
    );
  }
  return (
    <div ref={frameRef} className={`preview ${playing ? "is-playing" : ""} ${shown ? "is-revealed" : ""}`} onMouseLeave={veiled ? leave : undefined} style={{ "--ar": ar } as CSSProperties}>
      {media.type === "video" ? (
        <video
          ref={videoRef}
          controls
          playsInline
          // a private project is silent until asked: the controls unmute it
          muted={isPrivate}
          preload={veiled && !shown ? "none" : "metadata"}
          poster={veiled ? undefined : thumbUrl(slug, media.id, "poster")}
          src={mediaUrl(slug, media.id)}
          onPlay={() => {
            setPlaying(true);
            onPlayed?.();
          }}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
        />
      ) : null}
      {media.type === "video" ? (
        hidden && <button type="button" className="preview-reveal" onClick={reveal} title="hidden — click to show" aria-label="Show this video" />
      ) : (
        <img
          src={isGif(media.path) ? mediaUrl(slug, media.id) : thumbUrl(slug, media.id, "detail")}
          alt=""
          className="is-tappable"
          role="button"
          tabIndex={0}
          title={hidden ? "hidden — click to show" : "open full size"}
          onClick={open}
          onKeyDown={(e) => {
            if (e.key === "Enter") open();
          }}
        />
      )}
      {badge && <span className="preview-badge">{badge}</span>}
    </div>
  );
}
