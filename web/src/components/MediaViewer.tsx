import { useCallback, useEffect, useLayoutEffect, useRef, useState, type MouseEvent, type TouchEvent } from "react";
import { downloadUrl, errorText, mediaUrl, thumbUrl, useBin, useBins, useCommand, useMarkSeen, useMedia } from "../api/client";
import { useNavigate, useParams } from "react-router-dom";
import { recallPath, useMediaViewer } from "../layout/useMediaViewer";
import { useVeil } from "../layout/veil";
import { AudioPlayer } from "./AudioPlayer";
import { ConfirmSheet } from "./ConfirmSheet";
import { useToast } from "./Toast";
import { mediaLabel } from "./mediaLabel";
import { StartFromSheet } from "./StartFromSheet";
import { VeilGlyph } from "./VeilGlyph";

/** How far a finger must travel sideways to count as a swipe rather than a tap. */
const SWIPE_MIN_PX = 50;

/**
 * Full-screen preview of one media, above every panel. Images load at their original
 * size: fit to the screen by default, tap for actual size, pinch to zoom on a phone
 * (native, the body allows it). Video plays in the native player. ‹, Escape, or the
 * browser's back closes it.
 */
export function MediaViewer() {
  const viewer = useMediaViewer();
  // a private project: the video gets no poster, and the header carries the same padlock the
  // system bar does, so the veil can be lifted without leaving the picture
  const { veiled, revealed, toggle } = useVeil();
  const isPrivate = veiled || revealed;
  const navigate = useNavigate();
  const media = useMedia(viewer.slug ?? undefined, viewer.id ?? undefined);
  const [zoomed, setZoomed] = useState(false);
  // where the tap that zoomed landed, as a fraction of the picture: actual size opens on that
  // point rather than the top-left corner
  const bodyRef = useRef<HTMLDivElement>(null);
  /** Where a swipe began, or null when the gesture is not one (a pinch, a zoomed pan, the
   * video's controls). A ref, not state: it must not redraw the picture mid-drag. */
  const touch = useRef<{ x: number; y: number; multi: boolean } | null>(null);
  const focus = useRef({ fx: 0.5, fy: 0.5 });
  const toggleZoom = (e: MouseEvent<HTMLImageElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    focus.current = { fx: (e.clientX - r.left) / r.width, fy: (e.clientY - r.top) / r.height };
    setZoomed((z) => !z);
  };
  useLayoutEffect(() => {
    const body = bodyRef.current;
    const img = body?.querySelector("img");
    if (!zoomed || !body || !img) return;
    // the scroll box centres the tapped point; a picture smaller than the box on an axis
    // cannot scroll there and the auto margins centre it instead
    body.scrollLeft = focus.current.fx * img.offsetWidth - body.clientWidth / 2;
    body.scrollTop = focus.current.fy * img.offsetHeight - body.clientHeight / 2;
  }, [zoomed]);
  const [playing, setPlaying] = useState(false); // a veiled video stays visible only while it plays
  const [startFrom, setStartFrom] = useState(false); // the "generate from this" sheet, over the picture
  const [pendingDelete, setPendingDelete] = useState(false);
  // a cull run: deletes stop asking until the viewer is closed. Armed only through its own
  // warning, and never carried into the next visit -- see the reset below.
  const [noWarn, setNoWarn] = useState(false);
  const [askNoWarn, setAskNoWarn] = useState(false);
  const toast = useToast();
  const { id, close } = viewer;
  // the bin the viewer sits over, when it is a bin screen: the sheet's default destination
  const { binId: screenBin } = useParams();
  const bins = useBins(viewer.slug ?? undefined);
  // The slug outlives the close: `?view=` is gone the moment the viewer steps off a media, and
  // anything still in flight -- the seen mark on the way out, the delete that closed it -- would
  // otherwise address /api/projects/undefined. Every mutation here reads the project from here.
  const slugRef = useRef(viewer.slug);
  if (viewer.slug) slugRef.current = viewer.slug;
  const command = useCommand(slugRef.current ?? undefined);
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
    setStartFrom(false);
    setPendingDelete(false);
  }, [id]);
  // the cull run ends with the viewer, not with the media: stepping from one take to the next
  // keeps it armed, closing turns it off, and the next visit starts asking again
  useEffect(() => {
    if (!id) setNoWarn(false);
  }, [id]);

  /**
   * Where ‹ and › go: the bin's gallery order, newest first, the way the strip reads. Computed
   * before the early return so the arrow keys can use it. `nextUp` is where a delete lands --
   * the next take, or the one before it when the last is going.
   */
  const m = media.data;
  // reference mode (opened from a reference tile): the reference and the media it is bound to;
  // paging walks the bin that media lives in, wherever the viewer was opened from
  const forDetail = useBin(viewer.slug ?? undefined, viewer.refId ? viewer.refFor ?? undefined : undefined);
  const bound = viewer.refId ? forDetail.data?.references.find((r) => r.id === viewer.refId) : undefined;
  const boundMedia = bound?.media ?? null;
  const refHome = boundMedia
    ? boundMedia.origin?.bin ?? (bins.data ?? []).find((b) => (b.media ?? []).includes(boundMedia.id))?.id ?? bound?.bin_id ?? null
    : null;
  const homeBin = m ? (bins.data ?? []).find((b) => b.id === (refHome ?? screenBin ?? m.origin?.bin)) : undefined;
  const gallery = homeBin ? [...(homeBin.media ?? [])].reverse() : [];
  const at = m ? gallery.indexOf(m.id) : -1;
  const prevId = at > 0 ? gallery[at - 1] : null;
  const nextId = at >= 0 ? gallery[at + 1] ?? null : null;
  const nextUp = nextId ?? prevId;
  // a step replaces rather than pushes: browsing must not stack history the ‹ close then walks
  // back through, and a media that was just deleted must not be reachable by the browser's back
  const openViewer = viewer.open;
  const viewerSlug = viewer.slug;
  const step = useCallback(
    (to: string | null) => {
      if (to && viewerSlug) openViewer(viewerSlug, to, true);
    },
    [openViewer, viewerSlug],
  );
  useEffect(() => {
    if (!id) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight") step(nextId);
      else if (e.key === "ArrowLeft") step(prevId);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [id, close, step, nextId, prevId]);

  if (!viewer.slug || !viewer.id) return null;
  const name = m ? m.path.split("/").pop() : viewer.id;
  const dims = m?.width && m?.height ? `${m.width}×${m.height}` : "";
  // the two facts stand apart so the phone can drop the size and keep the length: a pixel size
  // is desk information, a duration tells you what you are about to watch
  const runtime = m?.duration_s ? `${m.duration_s.toFixed(1)}s` : "";
  const sound = m?.type === "audio" && m.sample_rate ? `${(m.sample_rate / 1000).toFixed(1)} kHz${m.channels ? ` · ${m.channels === 1 ? "mono" : m.channels === 2 ? "stereo" : `${m.channels} ch`}` : ""}` : "";
  /** Recall replaces the viewer with the bin the take came from, so back does not reopen the viewer. */
  const recall = m && viewer.slug ? recallPath(viewer.slug, m) : null;
  const doRecall = () => {
    if (recall) navigate(recall, { replace: true });
  };
  // the sheet's default is the bin on screen, else the bin the media landed in; its label is
  // the bin's word for the media (`take 3`, `#2`) when that bin is known, else the file name
  const label = m && homeBin ? mediaLabel(homeBin, m) : name ?? "this";
  const slug = viewer.slug;
  /** Deleting cascades out of every bin, reference and gallery; the file itself stays on disk.
   * The viewer stays open on the next take so a cull is one tap per media, not a round trip. */
  const doDelete = () => {
    if (!m) return;
    setPendingDelete(false);
    // the command goes first: on the last media there is no next, and closing the viewer
    // unmounts this component -- a mutate issued after that never leaves the browser
    command.mutate(
      { name: "delete_media", body: { media: m.id } },
      { onSuccess: (r) => toast(r.change?.description ?? "deleted"), onError: (err) => toast(errorText(err)) },
    );
    if (nextUp) viewer.open(slug, nextUp, true);
    else close();
  };
  /**
   * Swipe on a phone: left for the next take, right for the previous. Only a single finger that
   * travelled mostly sideways counts -- a pinch (the body allows native zoom), a scroll of an
   * actual-size picture, and anything starting on the video's own controls are left alone.
   */
  const onTouchStart = (e: TouchEvent<HTMLDivElement>) => {
    const t = e.touches[0];
    touch.current =
      zoomed || e.touches.length !== 1 || (e.target as HTMLElement).closest("video") ? null : { x: t.clientX, y: t.clientY, multi: false };
  };
  const onTouchMove = (e: TouchEvent<HTMLDivElement>) => {
    if (e.touches.length > 1 && touch.current) touch.current.multi = true;
  };
  const onTouchEnd = (e: TouchEvent<HTMLDivElement>) => {
    const from = touch.current;
    touch.current = null;
    if (!from || from.multi) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - from.x;
    const dy = t.clientY - from.y;
    if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    step(dx < 0 ? nextId : prevId);
  };

  const isCurrent = !!m && !!boundMedia && m.id === boundMedia.id;
  const canUse = !!m && !!boundMedia && !isCurrent && m.type === boundMedia.type;
  /** Swap the reference to this media: same slot, same @name. Back to the edit view. */
  const useAsReference = () => {
    if (!m || !bound || !viewer.refFor) return;
    command.mutate(
      { name: "rebind_reference", body: { bin: viewer.refFor, reference: bound.id, media: m.id } },
      {
        onSuccess: (r) => {
          toast(r.change?.description ?? `@${bound.handle} is now ${label}`);
          close();
        },
        onError: (err) => toast(errorText(err)),
      },
    );
  };
  /** The whole bin, in the picking flow: generate a new one there, or pick from the gallery. */
  const openRefBin = () => {
    if (!refHome || !viewer.refFor || !viewer.refId) return;
    const back = `/p/${encodeURIComponent(slug)}/b/${encodeURIComponent(viewer.refFor)}?generate`;
    navigate(
      `/p/${encodeURIComponent(slug)}/b/${encodeURIComponent(refHome)}?for=${encodeURIComponent(viewer.refFor)}&ref=${encodeURIComponent(viewer.refId)}&back=${encodeURIComponent(back)}`,
      { replace: true },
    );
  };

  /** Like recall: the viewer gives way to the bin's recipe, so back does not reopen it. */
  const startedIn = (binId: string) => {
    setStartFrom(false);
    navigate(`/p/${encodeURIComponent(slug)}/b/${encodeURIComponent(binId)}?generate`, { replace: true });
  };

  return (
    <div className={`viewer ${zoomed ? "is-zoomed" : ""}`} role="dialog" aria-label="Media preview">
      <header className="viewer-head">
        <button type="button" className="back viewer-back" onClick={close} aria-label="Back">
          ‹
        </button>
        {homeBin?.unseen?.includes(m!.id) && <span className="viewer-new" role="img" aria-label="new, not yet viewed" title="new — not yet viewed" />}
        <b className="viewer-name">{name}</b>
        <span className="spacer" />
        {bound && (
          <span className="viewer-ref" title={isCurrent ? `@${bound.handle} uses this one now` : `@${bound.handle} uses another one now`}>
            {isCurrent && <span className="viewer-current" aria-label="current reference" />}@{bound.handle}
          </span>
        )}
        {canUse && (
          <button type="button" className="viewer-recall is-accent" onClick={useAsReference} disabled={command.isPending} title={`make @${bound?.handle} use this one — same slot, same name`}>
            ✓ <span className="pill-word">Use as reference</span>
          </button>
        )}
        {bound && refHome && (
          <button type="button" className="viewer-recall" onClick={openRefBin} title="open the whole bin: generate a new one there, or pick from the gallery">
            ▤ <span className="pill-word">Open bin ›</span>
          </button>
        )}
        {dims && <span className="viewer-meta viewer-dims">{dims}</span>}
        {sound && <span className="viewer-meta viewer-dims">{sound}</span>}
        {runtime && <span className="viewer-meta">{runtime}</span>}
        {isPrivate && (
          <button
            type="button"
            className={`viewer-veil ${revealed ? "is-open" : ""}`}
            onClick={toggle}
            aria-pressed={revealed}
            aria-label={revealed ? "hide this project's pictures" : "show this project's pictures"}
            title={revealed ? "unlocked — tap to hide again; it hides on its own when you leave, when the window loses focus, or after 20 s idle" : "locked — tap to show this project's pictures"}
          >
            <VeilGlyph open={revealed} />
          </button>
        )}
        {m && (
          <button
            type="button"
            className="viewer-recall"
            onClick={() => setStartFrom(true)}
            aria-label="Reuse as a reference"
            title="reuse this as a reference for a new generation — in this bin, another, or a new one"
          >
            ✦ <span className="pill-word">Reuse</span>
          </button>
        )}
        {recall && (
          <button type="button" className="viewer-recall" onClick={doRecall} aria-label="Recall these settings" title="load these settings into the creator">
            ⟳ <span className="pill-word">Recall</span>
          </button>
        )}
        {m && (
          <a
            className="viewer-recall"
            href={downloadUrl(viewer.slug, m.id)}
            download
            aria-label="Download the original file"
            title="download the original file, in its own format"
          >
            ⤓ <span className="pill-word">Download</span>
          </a>
        )}
        {m && (
          <button
            type="button"
            className="viewer-recall is-danger"
            onClick={() => (noWarn ? doDelete() : setPendingDelete(true))}
            aria-label="Delete this media"
            title={noWarn ? "delete this media everywhere — no confirm while the run is on" : "delete this media everywhere — the next take takes its place here"}
          >
            ✕ <span className="pill-word">Delete</span>
          </button>
        )}
        {m && (
          <label className="viewer-nowarn" title="delete without the confirm, until this viewer is closed">
            <input
              type="checkbox"
              checked={noWarn}
              onChange={(e) => (e.target.checked ? setAskNoWarn(true) : setNoWarn(false))}
              aria-label="Bulk delete: no confirm until the viewer closes"
            />
            bulk
          </label>
        )}
      </header>
      <div
        className={`viewer-body ${playing ? "is-playing" : ""}`}
        ref={bodyRef}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
      >
        {media.isLoading && <div className="hint viewer-hint">loading…</div>}
        {media.error && <div className="hint viewer-hint">could not load this media</div>}
        {m?.type === "video" && (
          <video
            controls
            autoPlay
            playsInline
            // a private project is silent until asked: the controls unmute it
            muted={veiled}
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
            onClick={toggleZoom}
            title={zoomed ? "tap to fit the screen" : "tap for actual size"}
          />
        )}
        {m?.type === "audio" && (
          <AudioPlayer
            src={mediaUrl(viewer.slug, m.id)}
            // the waveform is a picture of the sound: under the veil it stays out of sight
            waveform={veiled ? null : thumbUrl(viewer.slug, m.id, "detail")}
            duration={m.duration_s}
            autoPlay
            muted={veiled}
            onPlay={() => {
              setPlaying(true);
              seen(m.id);
            }}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
          />
        )}
      </div>
      {prevId && (
        <button type="button" className="viewer-step is-prev" onClick={() => step(prevId)} aria-label="Previous media" title="previous (←, or swipe right)">
          ‹
        </button>
      )}
      {nextId && (
        <button type="button" className="viewer-step is-next" onClick={() => step(nextId)} aria-label="Next media" title="next (→, or swipe left)">
          ›
        </button>
      )}
      {askNoWarn && (
        <ConfirmSheet
          title="Delete without asking?"
          detail="For the rest of this viewer session, ✕ Delete removes the media at once, with no confirm — the viewer steps to the next take each time. Closing the viewer turns the warnings back on."
          confirmLabel="Stop asking"
          onConfirm={() => {
            setAskNoWarn(false);
            setNoWarn(true);
          }}
          onCancel={() => setAskNoWarn(false)}
        />
      )}
      {pendingDelete && m && (
        <ConfirmSheet
          title={`Delete ${label}?`}
          detail="It leaves this bin, every other bin holding it, and every reference using it. A generated file stays on disk. This cannot be undone."
          confirmLabel="Delete media"
          onConfirm={doDelete}
          onCancel={() => setPendingDelete(false)}
        />
      )}
      {startFrom && m && (
        <StartFromSheet
          slug={slug}
          bins={bins.data ?? []}
          mediaId={m.id}
          mediaLabel={label}
          defaultBin={homeBin?.id}
          onDone={startedIn}
          onClose={() => setStartFrom(false)}
        />
      )}
    </div>
  );
}
