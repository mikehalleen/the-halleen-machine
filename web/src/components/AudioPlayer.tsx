import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";

interface Props {
  /** The audio file (the media route, or a note file's route): served with Range, so seeks work. */
  src: string;
  /** The waveform picture across the strip (the media's `detail` thumbnail); null for a plain bar. */
  waveform?: string | null;
  /** Seconds, when the caller knows it; the element's own metadata wins once it has loaded. */
  duration?: number | null;
  autoPlay?: boolean;
  /** A private project is silent until asked: the play button still works, the sound starts off. */
  muted?: boolean;
  onPlay?: () => void;
  onPause?: () => void;
  onEnded?: () => void;
}

/** `0:03.4`, `1:23.0`: tenths, because a QC listen seeks to a word, not a minute. */
export function audioClock(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  const whole = Math.floor(rest);
  const tenth = Math.floor((rest - whole) * 10);
  return `${m}:${String(whole).padStart(2, "0")}.${tenth}`;
}

/**
 * The audio player: a waveform across the width with a playhead that follows `currentTime`, a
 * click anywhere on it to seek, play/pause and a time readout. No video element: audio is a
 * media type with sound and no picture. The waveform is the media's detail thumbnail; a note
 * file has none and gets a flat strip, which still seeks.
 */
export function AudioPlayer({ src, waveform, duration, autoPlay, muted, onPlay, onPause, onEnded }: Props) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [known, setKnown] = useState<number | null>(null);
  const total = known ?? (duration != null && duration > 0 ? duration : null);
  const frac = total ? Math.min(1, time / total) : 0;

  // a new source starts from the head; the element does the same, the state must follow
  useEffect(() => {
    setTime(0);
    setKnown(null);
    setPlaying(false);
  }, [src]);

  const toggle = () => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) void a.play().catch(() => undefined);
    else a.pause();
  };
  const seekTo = (fraction: number) => {
    const a = audioRef.current;
    if (!a || !total) return;
    const t = Math.max(0, Math.min(total, fraction * total));
    a.currentTime = t;
    setTime(t); // synchronous: a hidden pane may not fire timeupdate until it plays
  };
  const onWaveClick = (e: MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (r.width > 0) seekTo((e.clientX - r.left) / r.width);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      toggle();
    } else if (e.key === "ArrowRight" && total) {
      e.preventDefault();
      seekTo((time + 1) / total);
    } else if (e.key === "ArrowLeft" && total) {
      e.preventDefault();
      seekTo((time - 1) / total);
    }
  };

  return (
    <div className="audio-player" role="group" aria-label="Audio player">
      <audio
        ref={audioRef}
        src={src}
        preload="metadata"
        autoPlay={autoPlay}
        muted={muted}
        onLoadedMetadata={(e) => {
          const d = e.currentTarget.duration;
          if (Number.isFinite(d) && d > 0) setKnown(d);
        }}
        onDurationChange={(e) => {
          const d = e.currentTarget.duration;
          if (Number.isFinite(d) && d > 0) setKnown(d);
        }}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onSeeked={(e) => setTime(e.currentTarget.currentTime)}
        onPlay={() => {
          setPlaying(true);
          onPlay?.();
        }}
        onPause={() => {
          setPlaying(false);
          onPause?.();
        }}
        onEnded={() => {
          setPlaying(false);
          onEnded?.();
        }}
      />
      <div
        className={`audio-wave ${waveform ? "" : "is-flat"}`}
        style={waveform ? { backgroundImage: `url("${waveform}")` } : undefined}
        role="slider"
        tabIndex={0}
        aria-label="Position"
        aria-valuemin={0}
        aria-valuemax={total ?? 0}
        aria-valuenow={time}
        aria-valuetext={audioClock(time)}
        title="click to seek · space to play or pause · arrows to step a second"
        onClick={onWaveClick}
        onKeyDown={onKey}
      >
        <div className="audio-wave-played" style={{ width: `${frac * 100}%` }} />
        <div className="audio-playhead" style={{ left: `${frac * 100}%` }} />
      </div>
      <div className="audio-controls">
        <button type="button" className="audio-play" onClick={toggle} aria-label={playing ? "Pause" : "Play"} title={playing ? "pause" : "play"}>
          {playing ? "❚❚" : "▶"}
        </button>
        <span className="audio-time" aria-live="off">
          {audioClock(time)}
          <span className="audio-time-sep"> / </span>
          {total ? audioClock(total) : "–:––.–"}
        </span>
      </div>
    </div>
  );
}
