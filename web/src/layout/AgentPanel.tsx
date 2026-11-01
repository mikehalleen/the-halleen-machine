import { useCallback, useEffect, useState, type DragEvent, type FormEvent } from "react";
import { AGENT_FRESH_S, errorText, useAgentActivity, useAgentIdle, useBin, useClosePending, useCommand, useFeed } from "../api/client";
import type { BinCard, MessageContext, ProjectDetail, SessionMode, UploadOut } from "../api/types";
import { AutoTextarea } from "../components/AutoTextarea";
import { mediaLabel } from "../components/mediaLabel";
import { MediaThumb, ratioOf } from "../components/MediaThumb";
import { useToast } from "../components/Toast";
import { UploadSheet } from "../components/UploadSheet";
import { Feed } from "./Feed";
import { ModeMenu } from "./ModeMenu";

interface Props {
  slug: string;
  project?: ProjectDetail | null;
  /** The bin on screen, when the panel sits beside one: an upload from here lands in it. */
  binId?: string;
  /** The desk rail's ✕: close only, never end. Absent on the phone, where the bar's ▣ close does it. */
  onClose?: () => void;
  onSetMode: (mode: SessionMode) => void;
}

const ARM_MS = 5000;

/** A drag that carries files: a text selection dragged across the panel is not a drop. */
function dragHasFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

/**
 * The panel shell: header, feed, activity row, composer -- and nothing below the composer.
 * `end session` and `✕` are different acts at opposite ends of the header: one a worded pill
 * beside the title that asks twice, one a bare glyph that only closes. While a session is
 * waiting or live the composer is where notes and reference media go; after it ends the feed
 * stays as a read-only transcript and the bar's start control is the only way to a new one.
 *
 * Files dropped anywhere on the panel open the upload sheet with them, in every mode: the
 * user's uploads are never the agent's to gate.
 */
export function AgentPanel({ slug, project, binId, onClose, onSetMode }: Props) {
  const session = project?.agent_session ?? null;
  const status = session?.status;
  const active = status === "waiting" || status === "live";
  const feed = useFeed(slug);
  const command = useCommand(slug);
  const toast = useToast();
  const entries = feed.data?.entries ?? [];
  const activity = useAgentActivity(slug).data;
  const idle = useAgentIdle(activity);
  const doing = (activity?.doing ?? "").trim();
  // "waiting for you" is the agent's watch verb: standing by, not working
  const working = status === "live" && idle != null && idle < AGENT_FRESH_S && !!doing && !/^waiting/i.test(doing);
  const owedCards = project?.pending_changes ?? 0;
  const pending = owedCards + (project?.blocker ? 1 : 0);
  // clearing the cards at once: seen, nothing said, one feed line
  const closeAll = useClosePending(slug);
  const headDot = !active ? "is-ended" : pending > 0 ? "is-pending" : working ? "is-working" : "is-standby";
  const phrase = status === "waiting" ? "connecting…" : working ? doing : pending > 0 ? `holding · ${pending} pending` : "standing by";
  // what a note from here is about: the bin on screen and its lead media ("this take" means that).
  // Read from the bin screen's cache, so no fetch of its own; nothing on the home screen.
  const binDetail = useBin(slug, binId).data;
  const about: MessageContext | null = (() => {
    if (!binId || !binDetail) return null;
    const hero = binDetail.media.find((m) => m.id === binDetail.selected) ?? binDetail.media[0];
    return { bin: binDetail.bin.id, bin_name: binDetail.bin.name, media: hero?.id ?? null, media_label: hero ? mediaLabel(binDetail.bin, hero) : "" };
  })();

  // end session is a two-step: the first click arms the pill, the second (within 5s) ends
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), ARM_MS);
    return () => clearTimeout(t);
  }, [armed]);
  const endSession = () => {
    if (!armed) {
      setArmed(true);
      return;
    }
    setArmed(false);
    command.mutate(
      { name: "end_session", body: {} },
      { onSuccess: () => toast("session ended"), onError: (e) => toast(errorText(e)) },
    );
  };

  // a drop anywhere on the panel is an upload; the composer owns the sheet and the chips
  const [dropped, setDropped] = useState<File[] | null>(null);
  const onDroppedTaken = useCallback(() => setDropped(null), []);
  const [dragging, setDragging] = useState(false);
  const onDragOver = (e: DragEvent) => {
    if (!active || !dragHasFiles(e)) return;
    e.preventDefault();
    if (!dragging) setDragging(true);
  };
  const onDrop = (e: DragEvent) => {
    setDragging(false);
    if (!active || !dragHasFiles(e)) return;
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files);
    if (files.length) setDropped(files);
  };

  return (
    <div className={`agent-panel ${dragging ? "is-dropping" : ""}`} onDragOver={onDragOver} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
      <header className="agent-head">
        <span className={`dot ${headDot}`} aria-hidden="true" />
        <b className="agent-title">Agent session</b>
        {active && (
          <button
            type="button"
            className={`agent-end ${armed ? "is-armed" : ""}`}
            onClick={endSession}
            disabled={command.isPending}
            title={armed ? "click again to end the session" : "end the agent session (asks once more)"}
          >
            {armed ? "end session? click again" : "end session"}
          </button>
        )}
        <span className="spacer" />
        {active && session && <ModeMenu mode={session.mode} onChange={onSetMode} disabled={command.isPending} header />}
        {onClose && (
          <button type="button" className="agent-close" onClick={onClose} title="close the panel — the session keeps running" aria-label="Close the agent panel">
            ✕
          </button>
        )}
      </header>
      {feed.error && <div className="hint is-center">could not load the feed</div>}
      <Feed slug={slug} entries={entries} active={active} />
      {active && (
        <div className="agent-doing" aria-live="polite">
          <span className={`dot ${working ? "is-working" : "is-standby"}`} aria-hidden="true" />
          <span className="agent-doing-text">
            <span className="agent-doing-who">agent</span> · {phrase}
            {about && (
              <span className="agent-doing-about" title="a note from here is about what is on screen — the agent gets its id">
                ↳ {about.bin_name}
                {about.media_label ? ` · ${about.media_label}` : ""}
              </span>
            )}
            {owedCards > 0 && (
              <button
                type="button"
                className="link agent-doing-close"
                disabled={closeAll.isPending}
                title="close every pending card: seen, nothing sent"
                onClick={() =>
                  closeAll.mutate(undefined, {
                    onSuccess: (out) => toast(out.closed ? `closed ${out.closed} card${out.closed === 1 ? "" : "s"}` : "nothing pending"),
                    onError: (e) => toast(errorText(e)),
                  })
                }
              >
                close all
              </button>
            )}
          </span>
          {working && <span className="agent-doing-spinner" aria-hidden="true" />}
        </div>
      )}
      {active && <Composer slug={slug} bins={project?.bins ?? []} binId={binId} about={about} dropped={dropped} onDroppedTaken={onDroppedTaken} />}
    </div>
  );
}

interface ComposerProps {
  slug: string;
  bins: BinCard[];
  binId?: string;
  /** The bin and lead media on screen, sent with the note so "this take" resolves for the agent. */
  about: MessageContext | null;
  /** Files dropped on the panel: open the sheet with them. */
  dropped: File[] | null;
  onDroppedTaken: () => void;
}

/**
 * Notes and reference media, together: `+` (or a drop) uploads files into a bin through the
 * shared sheet, the uploads sit as chips above the note, and Send posts the note with their
 * media ids attached so the agent's inbox carries them. The upload lands the moment it is
 * confirmed -- the chips are the note's attachments, not a queue; ✕ on a chip only takes it off
 * the note. A note is still required: the server refuses an empty message.
 */
function Composer({ slug, bins, binId, about, dropped, onDroppedTaken }: ComposerProps) {
  const [text, setText] = useState("");
  const [attached, setAttached] = useState<UploadOut[]>([]);
  const [sheet, setSheet] = useState<File[] | null>(null); // open, with the files it opened with
  const command = useCommand(slug);
  const toast = useToast();
  useEffect(() => {
    if (!dropped) return;
    setSheet(dropped);
    onDroppedTaken();
  }, [dropped, onDroppedTaken]);
  const send = (e?: FormEvent) => {
    e?.preventDefault();
    const t = text.trim();
    if (!t || command.isPending) return;
    command.mutate(
      { name: "post_message", body: { text: t, kind: "user", attachments: attached.map((u) => u.media.id), ...(about ? { context: about } : {}) } },
      {
        onSuccess: () => {
          setText("");
          setAttached([]);
        },
        onError: (err) => toast(errorText(err)),
      },
    );
  };
  const uploaded = (results: UploadOut[]) => {
    setSheet(null);
    setAttached((prev) => [...prev, ...results.filter((r) => !prev.some((p) => p.media.id === r.media.id))]);
  };
  return (
    <>
      <form className="composer" onSubmit={send}>
        {attached.length > 0 && (
          <div className="composer-chips" aria-label="Attached media">
            {attached.map((u) => (
              <span key={u.media.id} className="composer-chip" title={`${u.media.path.split("/").pop()} · in ${u.bin.name}`}>
                <MediaThumb slug={slug} id={u.media.id} type={u.media.type} duration={u.media.duration_s} size={40} ratio={ratioOf(u.media)} passive />
                <button
                  type="button"
                  className="composer-chip-remove"
                  aria-label="Take off this note"
                  title={`take off this note — it stays in ${u.bin.name}`}
                  onClick={() => setAttached((prev) => prev.filter((p) => p.media.id !== u.media.id))}
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="composer-row">
          <button type="button" className="composer-attach" onClick={() => setSheet([])} title="upload reference media into a bin and attach it to this note" aria-label="Upload media">
            +
          </button>
          <AutoTextarea
            className="composer-input"
            value={text}
            onChange={setText}
            minRows={1}
            placeholder={attached.length ? "say what these are for…" : about ? `note about ${about.bin_name}…` : "note to the agent…"}
            aria-label="Note to the agent"
            onSubmitKey={send}
          />
          <button type="submit" className="composer-send" disabled={!text.trim() || command.isPending} title="send (Enter · Shift+Enter for a new line)">
            Send
          </button>
        </div>
      </form>
      {sheet && <UploadSheet slug={slug} bins={bins} defaultBin={binId} initialFiles={sheet} onDone={uploaded} onClose={() => setSheet(null)} />}
    </>
  );
}
