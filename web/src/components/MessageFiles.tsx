import { useEffect, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { chatFileUrl, useBins } from "../api/client";
import type { MessageFile } from "../api/types";
import { useVeil } from "../layout/veil";
import { AudioPlayer } from "./AudioPlayer";
import { AudioBadge, VideoBadge } from "./MediaThumb";
import { UploadSheet } from "./UploadSheet";

/**
 * A note's own files: what the user pasted or dropped into the composer to show the agent
 * something. They ride on the message and live in no bin, so nothing here opens the media
 * viewer or the library -- a tile opens the file itself, and `add to a bin` is the one way
 * into the project.
 */

/** One tile for a note's file. A still shows itself; a clip shows its name and the video mark
 * (no frame is fetched until it plays, which also keeps a private project's veil whole). */
export function FileTile({ slug, file, size = 72, onClick }: { slug: string; file: MessageFile; size?: number; onClick?: () => void }) {
  const onKey = (e: KeyboardEvent) => {
    if (onClick && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      onClick();
    }
  };
  return (
    <div
      className={`thumb file-tile ${onClick ? "is-tappable" : ""}`}
      style={{ width: size, height: size }}
      title={onClick ? `${file.name} — open full size` : file.name}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={onKey}
    >
      {file.type === "image" ? <img src={chatFileUrl(slug, file.id)} alt={file.name} loading="lazy" /> : <span className="file-tile-name">{file.name}</span>}
      {file.type === "video" && <VideoBadge />}
      {file.type === "audio" && <AudioBadge />}
    </div>
  );
}

/** The files on a feed line, full size on tap. */
export function MessageFiles({ slug, files }: { slug: string; files: MessageFile[] }) {
  const [open, setOpen] = useState<MessageFile | null>(null);
  if (!files.length) return null;
  return (
    <span className="row wrap bubble-attachments" aria-label="Files on this note">
      {files.map((f) => (
        <FileTile key={f.id} slug={slug} file={f} onClick={() => setOpen(f)} />
      ))}
      {open && <FileViewer slug={slug} file={open} onClose={() => setOpen(null)} />}
    </span>
  );
}

/** Full-screen view of one note file, in the media viewer's clothes. Portalled into the shell
 * so the rail's transform cannot clip it and a private project's veil still covers it. */
function FileViewer({ slug, file, onClose }: { slug: string; file: MessageFile; onClose: () => void }) {
  const { veiled } = useVeil();
  const bins = useBins(slug);
  const [playing, setPlaying] = useState(false);
  const [keeping, setKeeping] = useState(false);
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div className="viewer" role="dialog" aria-label="File on a note">
      <header className="viewer-head">
        <button type="button" className="back viewer-back" onClick={onClose} aria-label="Back">
          ‹
        </button>
        <b className="viewer-name">{file.name}</b>
        <span className="spacer" />
        <span className="viewer-meta">on this note only · not in the project</span>
        <button type="button" className="viewer-recall" onClick={() => setKeeping(true)} title="make this project media: add it to a bin">
          ＋ <span className="pill-word">Add to a bin</span>
        </button>
      </header>
      <div className={`viewer-body ${playing ? "is-playing" : ""}`}>
        {file.type === "audio" ? (
          // a note's sound file has no media record and so no waveform picture: a flat strip that still seeks
          <AudioPlayer src={chatFileUrl(slug, file.id)} autoPlay muted={veiled} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />
        ) : file.type === "video" ? (
          <video
            controls
            autoPlay
            playsInline
            // a private project is silent until asked: the controls unmute it
            muted={veiled}
            preload={veiled ? "none" : "metadata"}
            src={chatFileUrl(slug, file.id)}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
          />
        ) : (
          <img src={chatFileUrl(slug, file.id)} alt={file.name} />
        )}
      </div>
      {keeping && <UploadSheet slug={slug} bins={bins.data ?? []} keep={file} onDone={() => setKeeping(false)} onClose={() => setKeeping(false)} />}
    </div>,
    document.querySelector(".shell") ?? document.body,
  );
}
