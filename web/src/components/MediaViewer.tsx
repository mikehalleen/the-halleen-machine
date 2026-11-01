import { useEffect, useRef, useState } from "react";
import { mediaUrl, thumbUrl, useMarkSeen, useMedia } from "../api/client";
import { useNavigate } from "react-router-dom";
import { recallPath, useMediaViewer } from "../layout/useMediaViewer";
import { useVeil } from "../layout/veil";

/**
 * Full-screen preview of one media, above every panel. Images load at their original
 * size: fit to the screen by default, tap for actual size, pinch to zoom on a phone
 * (native, the body allows it). Video plays in the native player. ‹, Escape, or the
 * browser's back closes it.
 */
export function MediaViewer() {
  const viewer = useMediaViewer();
  const { veiled } = useVeil(); // a private project's video: no poster, the frame shows only when it plays
  const navigate = useNavigate();
  const media = useMedia(viewer.slug ?? undefined, viewer.id ?? undefined);
  const [zoomed, setZoomed] = useState(false);
  const [playing, setPlaying] = useState(false); // a veiled video stays visible only while it plays
  const { id, close } = viewer;
  // looked at, not merely opened: an image's new-take marker comes off when the viewer moves
  // on from it (closed, or another media), a video's when it plays. The slug outlives the
  // close, so the mark sent on the way out still knows its project.
  const slugRef = useRef(viewer.slug);
  if (viewer.slug) slugRef.current = viewer.slug;
  const markSeen = useMarkSeen(slugRef.current ?? undefined);
  const { mutate: seen } = markSeen;
  const type = media.data?.type;
  useEffect(() => {
    if (!id || type !== "image") return;
    return () => seen(id);
  }, [id, type, seen]);

  useEffect(() => {
    setZoomed(false);
    setPlaying(false);
  }, [id]);
  useEffect(() => {
    if (!id) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [id, close]);

  if (!viewer.slug || !viewer.id) return null;
  const m = media.data;
  const name = m ? m.path.split("/").pop() : viewer.id;
  const dims = m?.width && m?.height ? `${m.width}×${m.height}` : "";
  const meta = [dims, m?.duration_s ? `${m.duration_s.toFixed(1)}s` : ""].filter(Boolean).join(" · ");
  /** Recall replaces the viewer with the bin the take came from, so back does not reopen the viewer. */
  const recall = m && viewer.slug ? recallPath(viewer.slug, m) : null;
  const doRecall = () => {
    if (recall) navigate(recall, { replace: true });
  };

  return (
    <div className={`viewer ${zoomed ? "is-zoomed" : ""}`} role="dialog" aria-label="Media preview">
      <header className="viewer-head">
        <button type="button" className="back viewer-back" onClick={close} aria-label="Back">
          ‹
        </button>
        <b className="viewer-name">{name}</b>
        <span className="spacer" />
        {meta && <span className="viewer-meta">{meta}</span>}
        {recall && (
          <button type="button" className="viewer-recall" onClick={doRecall} title="load these settings into the creator">
            ⟳ Recall
          </button>
        )}
      </header>
      <div className={`viewer-body ${playing ? "is-playing" : ""}`}>
        {media.isLoading && <div className="hint viewer-hint">loading…</div>}
        {media.error && <div className="hint viewer-hint">could not load this media</div>}
        {m?.type === "video" && (
          <video
            controls
            autoPlay
            playsInline
            poster={veiled ? undefined : thumbUrl(viewer.slug, m.id, "poster")}
            src={mediaUrl(viewer.slug, m.id)}
            onPlay={() => {
              setPlaying(true);
              seen(m.id);
            }}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
          />
        )}
        {m?.type === "image" && (
          <img
            src={mediaUrl(viewer.slug, m.id)}
            alt={name}
            onClick={() => setZoomed((z) => !z)}
            title={zoomed ? "tap to fit the screen" : "tap for actual size"}
          />
        )}
      </div>
      {m?.type === "image" && <div className="viewer-foot">{zoomed ? "actual size · tap to fit · pinch to zoom" : "fit · tap for actual size · pinch to zoom"}</div>}
    </div>
  );
}
