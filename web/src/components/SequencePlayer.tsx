import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { mediaUrl, thumbUrl, useMarkSeen } from "../api/client";
import type { SequenceItemOut } from "../api/types";
import { useBreakpoint } from "../layout/useBreakpoint";
import { useClickReveal, useOnVeilDrop, useTapOutside, useVeil } from "../layout/veil";

/** The fades a free-text setting mentions, each as `0.3s fade in`: "0.3 s fade in", "fade-out
 * (0.8 s)", "fades out over 1.5 s" all count. The rest of the sentence is the agent's prose. */
function fadesIn(text: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const add = (n: string, dir: string) => {
    const line = `${Number(n)}s fade ${dir}`;
    if (!seen.has(line)) {
      seen.add(line);
      out.push(line);
    }
  };
  // "0.3 s fade in" / "0.8 s fade-out"
  for (const m of text.matchAll(/(\d+(?:\.\d+)?)\s*s\s*fade[- ]?(in|out)\b/gi)) add(m[1], m[2].toLowerCase());
  // "fade-in (0.3 s)" / "fade in over 0.3 s"
  for (const m of text.matchAll(/fade[- ]?(in|out)\b[^.;,]*?(\d+(?:\.\d+)?)\s*s\b/gi)) add(m[2], m[1].toLowerCase());
  // "fades out over 1.5 s"
  for (const m of text.matchAll(/fades?\s+(in|out)\s+over\s+(\d+(?:\.\d+)?)\s*s\b/gi)) add(m[2], m[1].toLowerCase());
  return out;
}

/** A setting in a few words for the player's note: `walla` for `walla_bed: "walla v5 …"`, plus any
 * fades its text names (`0.3s fade in`); `0.3s fade in` for `fade_in_s: 0.3`; `-6dB gain` for
 * `gain_db: -6`. Without a value, the kind alone. One setting can yield several entries. */
export function agentShort(key: string, value?: unknown): string[] {
  const words = key.replace(/_(s|db|ms)$/, "").split("_");
  if (typeof value === "string") return [words[0], ...fadesIn(value)];
  if (value === undefined || typeof value === "boolean" || value === null) return [words[0]];
  const label = words.join(" ");
  if (key.endsWith("_s")) return [`${value}s ${label}`];
  if (key.endsWith("_ms")) return [`${value}ms ${label}`];
  if (key.endsWith("_db")) return [`${Number(value) > 0 ? "+" : ""}${value}dB ${label}`];
  return [`${label} ${value}`];
}

/** `0:16.2` — seconds to tenths, the way the sequence screen reads time. */
export function seqClock(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${m}:${rest < 10 ? "0" : ""}${rest.toFixed(1)}`;
}

const CAP_KEY = "thm.playerHeight.sequence";
const CAP_MIN = 120;

interface Props {
  slug: string;
  items: SequenceItemOut[];
  /** The agent's sequence-wide settings (a walla bed, room tone): its summary shows under every item. */
  layer?: Record<string, unknown>;
  totalS: number;
  /** The item the player is on (its bin). The screen owns it: a tap on a row parks here. */
  current: string | null;
  /** The player moved on its own (play-through, |‹ ›|): the screen follows with the list. */
  onCurrent: (bin: string) => void;
  /** The project canvas ratio, for an item with no media of its own (a title card). */
  canvasRatio?: number;
}

/**
 * The stepping preview: item 1 plays, then 2, and so on. A video is the take itself (seeked past
 * its head trim and stopped at its out point); a still and a no-take title card are timed. Not
 * gapless and not meant to be: the export is where the cut is real, and the agent's settings on
 * an item (sound, gain, a dissolve) are not heard here at all.
 *
 * The frame is a `.preview` like the bin's hero: it takes the media's own shape (a portrait take
 * gets a portrait frame) under the same height cap, and under a private project's veil it is
 * hidden until clicked or played, and then shown across items until the mouse leaves the player.
 */
export function SequencePlayer({ slug, items, layer, totalS, current, onCurrent, canvasRatio = 16 / 9 }: Props) {
  const { veiled, revealed } = useVeil();
  // a private project: silent until the toggle says otherwise. Keyed on the project being
  // private, not on the veil's moment-to-moment state, so lifting the veil never unmutes
  const isPrivate = veiled || revealed;
  const [playing, setPlaying] = useState(false);
  const [elapsed, setElapsed] = useState(0); // seconds into the current item
  const [soundOn, setSoundOn] = useState(!isPrivate);
  useEffect(() => setSoundOn(!isPrivate), [isPrivate]);
  const videoRef = useRef<HTMLVideoElement>(null);
  const frame = useRef<number | null>(null);
  const playingRef = useRef(false);

  const atRaw = current ? items.findIndex((i) => i.bin === current) : -1;
  const at = atRaw >= 0 ? atRaw : 0;
  const item = items[at];
  const isVideo = !!item && item.kind === "video" && !!item.media;
  const head = item && !item.trim_dropped ? item.head_s : 0;
  // playing an item is looking at its take: it stops being new (that one only, as it plays)
  const { mutate: seen } = useMarkSeen(slug);
  const playedId = playing && item?.media ? item.media.id : null;
  useEffect(() => {
    if (playedId) seen(playedId);
  }, [playedId, seen]);
  // one reveal for the whole player, not one per item: lifted on clip 1, it stays lifted through
  // clip 2 and the stills between, until the mouse leaves the player area (or the veil's idle lock)
  const [shown, reveal, conceal] = useClickReveal("sequence-player");
  const hidden = veiled && !shown && !(isVideo && playing);
  /** Back under the veil: paused, hidden and silent. Playing is what lifted the veil, so the
   * pause is part of restoring it; the sound toggle is an explicit choice each time the frame is
   * shown again. Runs when the mouse leaves the player area, and whenever the veil drops (the
   * bar's padlock, idle, the window losing focus), which the stylesheet alone cannot do to a
   * playing video. */
  const drop = () => {
    if (!isPrivate) return;
    stop();
    conceal();
    setSoundOn(false);
  };
  const leave = () => {
    if (veiled) drop();
  };
  useOnVeilDrop(drop);
  // a phone has no mouse to leave with: a tap outside the frame and its controls does the same
  const zoneRef = useRef<HTMLDivElement>(null);
  useTapOutside(zoneRef, veiled && (shown || playing), drop);

  const clearFrame = () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
  };
  const stop = useCallback(() => {
    clearFrame();
    playingRef.current = false;
    setPlaying(false);
    const v = videoRef.current;
    if (v && !v.paused) v.pause();
  }, []);
  useEffect(() => stop, [stop]);

  // a tap on a row parks the player there, paused, from the item's start
  const lastCurrent = useRef<string | null>(null);
  useEffect(() => {
    if (current === lastCurrent.current) return;
    lastCurrent.current = current;
    setElapsed(0);
  }, [current]);
  // under the veil a frame shows while it plays: pressing play is the reveal
  useEffect(() => {
    if (playing && veiled && !shown) reveal();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);
  // the list changed under the player (a drag, a remove): stay on a valid item
  useEffect(() => {
    if (items.length && atRaw < 0 && current !== null) onCurrent(items[0].bin);
  }, [items, atRaw, current, onCurrent]);

  const go = useCallback(
    (index: number, keepPlaying: boolean) => {
      const next = items[index];
      if (!next) {
        stop();
        setElapsed(items[items.length - 1]?.duration_s ?? 0);
        return;
      }
      setElapsed(0);
      if (!keepPlaying) stop();
      onCurrent(next.bin);
    },
    [items, onCurrent, stop],
  );
  const advance = useCallback(() => go(at + 1, true), [go, at]);

  // drive the current item while playing: a timer for a still or a card, the video's own clock for a clip
  useEffect(() => {
    if (!playing || !item) return;
    clearFrame();
    if (!isVideo) {
      const started = performance.now();
      const tick = () => {
        const t = (performance.now() - started) / 1000;
        if (t >= item.duration_s) {
          advance();
          return;
        }
        setElapsed(t);
        frame.current = requestAnimationFrame(tick);
      };
      frame.current = requestAnimationFrame(tick);
      return clearFrame;
    }
    const v = videoRef.current;
    if (!v) return;
    const start = () => {
      v.currentTime = head;
      void v.play().catch(() => stop());
    };
    if (v.readyState >= 1) start();
    else v.addEventListener("loadedmetadata", start, { once: true });
    const watch = () => {
      if (!playingRef.current) return;
      const t = v.currentTime - head;
      setElapsed(Math.max(0, t));
      if (t >= item.duration_s - 0.02 || v.ended) {
        v.pause();
        advance();
        return;
      }
      frame.current = requestAnimationFrame(watch);
    };
    frame.current = requestAnimationFrame(watch);
    return () => {
      v.removeEventListener("loadedmetadata", start);
      clearFrame();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, at, item?.bin, item?.media?.id, isVideo]);

  const play = () => {
    if (!items.length) return;
    if (!current) onCurrent(items[0].bin);
    playingRef.current = true;
    setPlaying(true);
  };

  // the frame's height cap, dragged by the edge under the player and kept on this device; never
  // dragged, the .preview default applies
  const [cap, setCapState] = useState<number | null>(() => {
    try {
      const v = Number(localStorage.getItem(CAP_KEY));
      return Number.isFinite(v) && v > 0 ? v : null;
    } catch {
      return null;
    }
  });
  const setCap = (px: number | null) => {
    setCapState(px);
    try {
      if (px === null) localStorage.removeItem(CAP_KEY);
      else localStorage.setItem(CAP_KEY, String(Math.round(px)));
    } catch {
      // a remembered height is a convenience
    }
  };
  const resize = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    const from = zoneRef.current?.querySelector(".seq-player")?.getBoundingClientRect().height ?? 0;
    const y0 = e.clientY;
    // the rows keep some room under it
    const body = handle.closest(".screen-body")?.getBoundingClientRect().height ?? window.innerHeight;
    const max = Math.max(CAP_MIN, body - 200);
    document.body.classList.add("is-resizing-player");
    const move = (ev: PointerEvent) => setCap(Math.min(max, Math.max(CAP_MIN, from + ev.clientY - y0)));
    const end = () => {
      document.body.classList.remove("is-resizing-player");
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  };

  const before = items.slice(0, at).reduce((n, i) => n + i.duration_s, 0);
  const position = Math.min(totalS, before + elapsed);
  const m = item?.media;
  const ratio = m?.width && m?.height ? m.width / m.height : canvasRatio;
  const style = { "--ar": Math.min(2, Math.max(0.5, ratio)), ...(cap ? { "--preview-cap": `${cap}px` } : {}) } as CSSProperties;
  const card = item?.kind === "title";
  // the agent's settings are not in this preview: "export only" lines in the agent's own words
  // (its `summary`). The sequence-wide layer shows under every item; an item's own line shows
  // only while that item is on screen, never carried to the items after it. Settings from
  // before summaries were required fall back to a list derived from their keys.
  const summaryOf = (agent: Record<string, unknown> | undefined): string => {
    const entries = Object.entries(agent ?? {});
    if (!entries.length) return "";
    const s = agent?.summary;
    if (typeof s === "string" && s.trim()) return s.trim();
    return Array.from(new Set(entries.flatMap(([k, v]) => agentShort(k, v)))).join(", ");
  };
  const layerText = summaryOf(layer);
  const hereText = summaryOf(item?.agent);
  // a phone with a portrait item: the notes go beside the frame, not under it, so more rows show
  const { wide } = useBreakpoint();
  const side = !wide && ratio < 1 && !!(layerText || hereText);

  return (
    <div className={`seq-player-wrap ${side ? "is-side" : ""}`} style={side ? style : undefined}>
      {/* the veil's rollover zone: the frame and its controls, not the band around them */}
      <div ref={zoneRef} className="seq-player-zone" style={style} onMouseLeave={veiled ? leave : undefined}>
      <div className={`preview seq-player ${playing ? "is-playing" : ""} ${shown ? "is-revealed" : ""}`}>
        {item && isVideo && item.media && (
          <video
            key={item.media.id}
            ref={videoRef}
            src={mediaUrl(slug, item.media.id)}
            poster={veiled ? undefined : thumbUrl(slug, item.media.id, "poster")}
            preload={veiled && !shown ? "none" : "metadata"}
            playsInline
            muted={!soundOn}
            onLoadedMetadata={(e) => {
              if (!playingRef.current) e.currentTarget.currentTime = head;
            }}
            onEnded={() => playingRef.current && advance()}
          />
        )}
        {item && item.kind === "still" && item.media && <img src={thumbUrl(slug, item.media.id, "detail")} alt="" />}
        {item && card && (
          <div className="seq-card-face">
            {item.shot != null && <span className="seq-card-no">{item.shot}</span>}
            <span className="seq-card-title">{item.name}</span>
          </div>
        )}
        {!item && <div className="seq-player-empty">no items yet</div>}
        {item && hidden && <button type="button" className="preview-reveal" onClick={reveal} title="hidden — click to show" aria-label="Show this item" />}
        {item && isVideo && (
          <button
            type="button"
            className="clip-sound seq-sound"
            aria-pressed={soundOn}
            aria-label={soundOn ? "Mute" : "Unmute"}
            title={soundOn ? "mute" : isPrivate ? "unmute — a private project plays silent by default" : "unmute"}
            onClick={(e) => {
              e.stopPropagation();
              setSoundOn(!soundOn);
            }}
          >
            {soundOn ? "🔊" : "🔇"}
          </button>
        )}
        {item && (
          <span className="seq-player-corner">
            {at + 1}/{items.length} · {item.shot != null ? `${item.shot} ` : ""}
            {item.name}
          </span>
        )}
      </div>
      <div className="seq-progress" aria-hidden="true">
        <div className="seq-progress-fill" style={{ width: `${totalS > 0 ? (position / totalS) * 100 : 0}%` }} />
      </div>
      <div className="row seq-controls">
        <button type="button" className="seq-ctl" onClick={() => go(at - 1, playingRef.current)} disabled={!items.length || at === 0} aria-label="Previous item" title="previous item">
          |‹
        </button>
        <button type="button" className="seq-ctl seq-ctl-play" onClick={() => (playing ? stop() : play())} disabled={!items.length} aria-label={playing ? "Pause" : "Play"} title={playing ? "pause" : "play through"}>
          {playing ? "❚❚" : "▶"}
        </button>
        <button type="button" className="seq-ctl" onClick={() => go(at + 1, playingRef.current)} disabled={!items.length || at >= items.length - 1} aria-label="Next item" title="next item">
          ›|
        </button>
        <span className="seq-time">
          {seqClock(position)} / {seqClock(totalS)}
        </span>
      </div>
      </div>
      {(layerText || hereText) && (
        <div className="seq-raw-notes">
          {layerText && (
            <div className="seq-raw-note" role="note" title="the agent's sequence-wide settings are not applied in this preview, only in the export">
              <span className="seq-raw-glyph" aria-hidden="true">
                ⚠
              </span>
              <span>export only, whole sequence: {layerText}</span>
            </div>
          )}
          {hereText && (
            <div className="seq-raw-note" role="note" title="the agent's settings on this item are not applied in this preview, only in the export">
              <span className="seq-raw-glyph" aria-hidden="true">
                ⚠
              </span>
              <span>export only: {hereText}</span>
            </div>
          )}
        </div>
      )}
      <div
        className="seq-player-resizer"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize the player"
        title="drag to resize · double-click to reset"
        onPointerDown={resize}
        onDoubleClick={() => setCap(null)}
      />
    </div>
  );
}
