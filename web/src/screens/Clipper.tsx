import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { useParams } from "react-router-dom";
import { errorText, mediaUrl, thumbUrl, useBin, useCreateClip, useMedia, useProject } from "../api/client";
import type { ClipBody, ClipMode } from "../api/types";
import { Hint } from "../components/Hint";
import { Tag } from "../components/Tag";
import { useToast } from "../components/Toast";
import { Shell } from "../layout/Shell";
import { useCloseTo } from "../layout/useCloseTo";

const MIN_SPAN_PCT = 4;
/** Close enough to the out point to stop on it: one frame at 60fps, under the gap between
 * animation frames, so playback lands on the out frame rather than overshooting past it. */
const STOP_EPSILON_S = 0.016;

function clamp(v: number): number {
  return Math.min(100, Math.max(0, v));
}

/** Cut a shorter segment or lift a still frame from an existing video (design's "clip and
 * still"). Full screen at every breakpoint, like the prototype — not adapted for wide layouts.
 * Reached from a bin's selected video; the result lands in that same bin.
 *
 * The preview is the source video itself, seeked: paused on the handle you last moved, or
 * played from in to out. Nothing is extracted to show it — the media route serves Range
 * (`FileResponse`), so a seek costs one byte-range read and one decoded frame. It is a
 * truthful preview because `extract_segment`/`extract_still` put `-ss` after `-i` and cut
 * frame-accurately rather than snapping to a keyframe. */
export function ClipperRoute() {
  const { slug, binId, mediaId } = useParams();
  const toast = useToast();

  const media = useMedia(slug, mediaId);
  const bin = useBin(slug, binId);
  const createClip = useCreateClip(slug);

  const [mode, setMode] = useState<ClipMode>("segment");
  const [inPct, setInPct] = useState(18);
  const [outPct, setOutPct] = useState(62);
  const [framePct, setFramePct] = useState(40);
  /** which end of the segment the preview is parked on — whichever you moved last */
  const [handle, setHandle] = useState<"in" | "out">("in");
  const [playing, setPlaying] = useState(false);

  const projectPath = `/p/${encodeURIComponent(slug ?? "")}`;
  const returnPath = binId ? `${projectPath}/b/${encodeURIComponent(binId)}` : projectPath;
  const close = useCloseTo(returnPath);
  // this screen renders its own Shell, so it is outside the veil context: a private project's
  // source video simply never gets a poster (the shell's stylesheet hides the frame on a phone)
  const veiled = !!useProject(slug).data?.private;

  const total = media.data?.duration_s ?? 0;
  const tIn = (total * inPct) / 100;
  const tOut = (total * outPct) / 100;
  const tPos = (total * framePct) / 100;
  const label =
    mode === "segment"
      ? `${tIn.toFixed(1)}s – ${tOut.toFixed(1)}s · ${(tOut - tIn).toFixed(1)}s clip of ${total.toFixed(1)}s`
      : `frame at ${tPos.toFixed(1)}s of ${total.toFixed(1)}s`;

  const previewAt = mode === "still" ? tPos : handle === "out" ? tOut : tIn;
  const previewLabel = mode === "still" ? `frame ${tPos.toFixed(1)}s` : handle === "out" ? `out ${tOut.toFixed(1)}s` : `in ${tIn.toFixed(1)}s`;

  const videoRef = useRef<HTMLVideoElement>(null);
  const frame = useRef<number | null>(null);
  const stopAt = useRef(0);
  const playingRef = useRef(false);
  // One seek in flight at a time: a dragged slider fires far faster than a decoder answers, so
  // the latest wanted time is remembered and re-issued from `seeked` instead of queueing every
  // intermediate position.
  const seeking = useRef(false);
  const wanted = useRef<number | null>(null);

  const setPlayState = (on: boolean) => {
    playingRef.current = on;
    setPlaying(on);
  };

  const seekTo = useCallback((t: number) => {
    const v = videoRef.current;
    if (!v || v.readyState < 1 || !Number.isFinite(t)) return;
    wanted.current = t;
    if (seeking.current) return;
    seeking.current = true;
    v.currentTime = t;
  }, []);

  const onSeeked = () => {
    seeking.current = false;
    if (playingRef.current) return;
    const v = videoRef.current;
    const want = wanted.current;
    if (v && want !== null && Math.abs(v.currentTime - want) > 0.03) {
      seeking.current = true;
      v.currentTime = want;
    }
  };

  const stopPlayback = useCallback(() => {
    if (frame.current !== null) {
      cancelAnimationFrame(frame.current);
      frame.current = null;
    }
    const v = videoRef.current;
    if (v && !v.paused) v.pause();
    playingRef.current = false;
    setPlaying(false);
  }, []);

  useEffect(() => stopPlayback, [stopPlayback]);

  // Park on the previewed time whenever it moves. Deliberately not keyed on `playing`: after a
  // segment finishes, the frame it stopped on is the out point, and re-seeking back to the in
  // point the moment playback ends would yank the picture out from under you.
  useEffect(() => {
    if (playingRef.current) return;
    seekTo(previewAt);
  }, [previewAt, seekTo]);

  /** True once playback is done — at or past the out point, or stopped by other means. */
  const checkStop = useCallback(() => {
    const v = videoRef.current;
    if (!v || !playingRef.current) return true;
    if (!v.paused && v.currentTime < stopAt.current - STOP_EPSILON_S) return false;
    v.pause();
    // the out point is now what the coalescer should hold: leaving it on the in point that
    // playback jumped to would have `seeked` drag the picture straight back there.
    wanted.current = stopAt.current;
    v.currentTime = stopAt.current;
    playingRef.current = false;
    setPlaying(false);
    return true;
  }, []);

  // Two watchers, because neither alone is enough: requestAnimationFrame stops at the out
  // frame but is suspended while the tab is hidden (playback would then run to the end of the
  // source), and timeupdate keeps firing when hidden but only ~4x a second, which overshoots.
  const watch = useCallback(() => {
    if (checkStop()) {
      frame.current = null;
      return;
    }
    frame.current = requestAnimationFrame(watch);
  }, [checkStop]);

  const playSegment = () => {
    const v = videoRef.current;
    if (!v || total <= 0) return;
    stopAt.current = tOut;
    wanted.current = tIn;
    seeking.current = false;
    v.currentTime = tIn;
    setPlayState(true);
    void v
      .play()
      .then(() => {
        frame.current = requestAnimationFrame(watch);
      })
      .catch(() => setPlayState(false));
  };

  const setIn = (v: number) => {
    stopPlayback();
    setHandle("in");
    setInPct(Math.min(clamp(v), outPct - MIN_SPAN_PCT));
  };
  const setOut = (v: number) => {
    stopPlayback();
    setHandle("out");
    setOutPct(Math.max(clamp(v), inPct + MIN_SPAN_PCT));
  };
  const setFrame = (v: number) => {
    stopPlayback();
    setFramePct(clamp(v));
  };
  const pickMode = (next: ClipMode) => {
    stopPlayback();
    setMode(next);
  };

  const onSave = () => {
    if (!mediaId || !binId) return;
    const body: ClipBody =
      mode === "segment"
        ? { source: mediaId, mode: "segment", start_s: tIn, end_s: tOut, bin: binId }
        : { source: mediaId, mode: "still", at_s: tPos, bin: binId };
    createClip.mutate(body, {
      onSuccess: (res) => {
        toast(`saved → ${res.bin.name} #${(res.bin.media ?? []).length} (${mode === "segment" ? "clip" : "still"}) — selected in the bin`);
        close();
      },
      onError: (e) => toast(errorText(e)),
    });
  };

  /** width ÷ height as a bare number, handed to CSS as `--ar`: the frame sizes itself from it
   * rather than being stretched full width and then letterboxed back down. */
  const ar = media.data?.width && media.data?.height ? media.data.width / media.data.height : 16 / 9;

  return (
    <Shell slug={slug} binId={binId}>
      <div className="screen" data-screen-label="Clipper">
        <header className="screen-head">
          <button type="button" className="back" aria-label="Close" onClick={close}>
            ✕
          </button>
          <b className="screen-title">Clip {media.data ? "media" : "…"}</b>
          <span className="spacer" />
          <Tag>CLIPPER</Tag>
        </header>
        <div className="screen-body">
          <div className={`preview clip-source ${playing ? "is-playing" : ""}`} style={{ "--ar": ar } as CSSProperties}>
            {media.data && mediaId && (
              <video
                ref={videoRef}
                src={mediaUrl(slug ?? "", mediaId)}
                poster={veiled ? undefined : thumbUrl(slug ?? "", mediaId, "poster")}
                preload="metadata"
                playsInline
                onLoadedMetadata={() => seekTo(previewAt)}
                onSeeked={onSeeked}
                onTimeUpdate={() => checkStop()}
                onEnded={() => setPlayState(false)}
                onPause={() => setPlayState(false)}
              />
            )}
            {mode === "segment" && total > 0 && (
              <button
                type="button"
                className="clip-play"
                aria-label={playing ? "Stop" : "Play the segment"}
                title={playing ? "Stop" : "Play in → out"}
                onClick={() => (playing ? stopPlayback() : playSegment())}
              >
                {playing ? "■" : "▶"}
              </button>
            )}
            {total > 0 && <span className="preview-badge">{previewLabel}</span>}
          </div>

          <div className="row chips">
            <button type="button" className={`chip ${mode === "segment" ? "is-on" : ""}`} onClick={() => pickMode("segment")}>
              ✂ shorter segment
            </button>
            <button type="button" className={`chip ${mode === "still" ? "is-on" : ""}`} onClick={() => pickMode("still")}>
              ◻ still image
            </button>
          </div>

          <div className="card clip-scrub">
            <div className="clip-timeline">
              {mode === "segment" ? (
                <div className="clip-fill" style={{ left: `${inPct}%`, width: `${outPct - inPct}%` }} />
              ) : (
                <div className="clip-playhead" style={{ left: `${framePct}%` }} />
              )}
            </div>
            {mode === "segment" ? (
              <>
                <label className="row">
                  <span className="clip-slider-label">IN</span>
                  <input type="range" min={0} max={100} value={inPct} onChange={(e) => setIn(Number(e.target.value))} />
                </label>
                <label className="row">
                  <span className="clip-slider-label">OUT</span>
                  <input type="range" min={0} max={100} value={outPct} onChange={(e) => setOut(Number(e.target.value))} />
                </label>
              </>
            ) : (
              <label className="row">
                <span className="clip-slider-label">FRAME</span>
                <input type="range" min={0} max={100} value={framePct} onChange={(e) => setFrame(Number(e.target.value))} />
              </label>
            )}
            <div className="clip-label">{label}</div>
          </div>

          <div className="row">
            <span className="tag">INTO</span>
            <b>.{bin.data?.bin.name ?? "…"}</b>
          </div>
          <Hint>the clip lands in this bin as ordinary media — usable as a reference anywhere</Hint>
        </div>
        <footer className="screen-foot">
          <button type="button" className="btn-primary btn-block" disabled={createClip.isPending || total <= 0} onClick={onSave}>
            {createClip.isPending ? "saving…" : mode === "segment" ? "Save clip to bin" : "Save still to bin"}
          </button>
        </footer>
      </div>
    </Shell>
  );
}
