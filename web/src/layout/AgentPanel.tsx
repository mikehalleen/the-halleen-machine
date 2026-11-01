import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import { AGENT_FRESH_S, errorText, removeChatFile, uploadChatFile, useAgentActivity, useAgentIdle, useBin, useClosePending, useCommand, useFeed, useSequence } from "../api/client";
import type { BinCard, MessageContext, MessageFile, ProjectDetail, SessionMode, UploadOut } from "../api/types";
import { AutoTextarea } from "../components/AutoTextarea";
import { mediaLabel } from "../components/mediaLabel";
import { MediaThumb, ratioOf } from "../components/MediaThumb";
import { FileTile } from "../components/MessageFiles";
import { useToast } from "../components/Toast";
import { UploadSheet } from "../components/UploadSheet";
import { useDraft } from "../components/useDraft";
import { useAgentUi } from "./AgentUi";
import { Feed, openCardAbout } from "./Feed";
import { requestJump } from "./jumpCard";
import { ModeMenu } from "./ModeMenu";
import { DRAFTS_HINT } from "./modes";
import { pastedInAgentPanel, usePasteFiles } from "./usePasteFiles";
import { useBreakpoint } from "./useBreakpoint";

interface Props {
  slug: string;
  project?: ProjectDetail | null;
  /** The bin on screen, when the panel sits beside one: an upload from here lands in it. */
  binId?: string;
  /** The sequence on screen: a note from here is about it, and about its open row when one is. */
  seqId?: string;
  /** The desk rail's ✕: close only, never end. Absent on the phone, where the bar's ▣ close does it. */
  onClose?: () => void;
  onSetMode: (mode: SessionMode) => void;
  onSetDrafts: (drafts: boolean) => void;
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
 * Files dropped anywhere on the panel go on the note being written, in every mode: what the
 * user shows the agent is never the agent's to gate, and never needs a bin.
 */
export function AgentPanel({ slug, project, binId, seqId, onClose, onSetMode, onSetDrafts }: Props) {
  // a phone's header has no room for the draft switch beside the mode: it moves into the menu
  const { wide } = useBreakpoint();
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
  // notes the agent read and has not dealt with: work it owes, said out loud so a stall shows
  const openNotes = project?.open_notes ?? 0;
  const owes = openNotes > 0 ? ` · ${openNotes} note${openNotes === 1 ? "" : "s"} open` : "";
  // the live word wins: the project detail's flag was true when that detail was fetched, and a
  // read-only heartbeat never refetches it
  const gone = status === "live" && (activity?.gone ?? !!project?.agent_gone) && !working;
  const phrase = status === "waiting" ? "connecting…" : working ? doing + owes : gone ? `agent away · waiting for one${owes}` : pending > 0 ? `holding · ${pending} pending${owes}` : openNotes > 0 ? `${openNotes} note${openNotes === 1 ? "" : "s"} open` : "standing by";
  // what a note from here is about: the bin on screen and its lead media ("this take" means that).
  // Read from the bin screen's cache, so no fetch of its own; nothing on the home screen.
  const binDetail = useBin(slug, binId).data;
  // on the sequence screen: the sequence, plus the open row's bin and take when a row is open
  const seq = useSequence(slug, seqId).data;
  const { openItem } = useAgentUi();
  const about: MessageContext | null = (() => {
    if (seqId && seq) {
      const item = openItem ? seq.items.find((i) => i.bin === openItem) : undefined;
      const m = item?.media ?? null;
      const label = m ? (m.take ? `take ${m.take}` : m.type === "video" ? "clip" : m.type === "audio" ? "audio" : "still") : item ? "no take" : "";
      return {
        sequence: seq.id,
        sequence_name: seq.name,
        bin: item?.bin ?? null,
        bin_name: item?.name ?? "",
        media: m?.id ?? null,
        media_label: label,
        position: item?.position ?? null,
      };
    }
    if (!binId || !binDetail) return null;
    const hero = binDetail.media.find((m) => m.id === binDetail.selected) ?? binDetail.media[0];
    return { bin: binDetail.bin.id, bin_name: binDetail.bin.name, media: hero?.id ?? null, media_label: hero ? mediaLabel(binDetail.bin, hero) : "", sequence: null, sequence_name: "", position: null };
  })();
  /** The about line as words: `Rough cut · #3 the screen · take 39`, or `the screen · take 39`. */
  const aboutText = about ? [about.sequence_name, about.bin_name ? `${about.position ? `#${about.position} ` : ""}${about.bin_name}` : "", about.media_label].filter(Boolean).join(" · ") : "";

  // the cards still waiting, oldest first; and the ones about the bin on screen, so a note
  // written while reviewing its takes does not talk past a card already asking about it
  const aboutOpen = useMemo(() => entries.filter((e) => openCardAbout(e, binId)).map((e) => e.change!.id), [entries, binId]);

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

  // a drop anywhere on the panel goes on the note; the composer owns the chips
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
  // a paste into the composer is a drop: the image goes on the note; a paste elsewhere on
  // the page is the screen's to take
  usePasteFiles((files, e) => (pastedInAgentPanel(e) ? void setDropped(files) : false), active);

  return (
    <div className={`agent-panel ${dragging ? "is-dropping" : ""}`} onDragOver={onDragOver} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
      <header className="agent-head">
        <span className={`dot ${headDot}`} aria-hidden="true" />
        <b className="agent-title">Agent</b>
        {active && (
          <button
            type="button"
            className={`agent-end ${armed ? "is-armed" : ""}`}
            onClick={endSession}
            disabled={command.isPending}
            title={armed ? "click again to end the session" : "end the agent session (asks once more)"}
          >
            {armed ? (wide ? "end session? click again" : "end? again") : wide ? "end session" : "end"}
          </button>
        )}
        <span className="spacer" />
        {active && session && (
          <>
            {wide && (
              <label className="draft-toggle" title={DRAFTS_HINT}>
                <input type="checkbox" checked={!!session.drafts} onChange={(e) => onSetDrafts(e.target.checked)} disabled={command.isPending} />
                draft mode
              </label>
            )}
            <ModeMenu
              mode={session.mode}
              onChange={onSetMode}
              drafts={wide ? undefined : { on: !!session.drafts, onChange: onSetDrafts }}
              disabled={command.isPending}
              header
            />
          </>
        )}
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
re: {aboutText}
              </span>
            )}
          </span>
          {/* the controls sit outside the phrase, which clips with an ellipsis: they must never be what gets clipped */}
          <span className="agent-doing-actions">
            {/* a card about the bin on screen is still waiting: say so before a note here talks past it */}
            {aboutOpen.length > 0 && (
              <button type="button" className="link agent-doing-card" onClick={() => requestJump(aboutOpen[0])} title="the agent already asked about this bin — read that card before you write">
                ⚠ card about this bin ↑
              </button>
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
  /** Files dropped on the panel: put them on the note. */
  dropped: File[] | null;
  onDroppedTaken: () => void;
}

/**
 * Notes and what they carry. A file pasted, dropped or picked with `+` goes on the note itself:
 * it uploads at once as the note's own file, in no bin, and sits as a chip above the text --
 * a marked-up screenshot for the agent is not project media and never has to become any.
 * `bin` on a chip is the way into the project: it asks which bin, the file lands there like an
 * upload, and the chip becomes that media attached to the note. ✕ takes a chip off the note.
 * Send posts the text with both kinds; a file alone is a note too (the server refuses only a
 * note with nothing in it).
 */
const UPLOADS_BIN = "uploads";

function Composer({ slug, bins, about, dropped, onDroppedTaken }: ComposerProps) {
  // the note survives a route change and a reload, and so do the files on it; sending clears both
  const [text, setText] = useDraft(`${slug}.composer`, "");
  const [files, setFiles] = useDraft<MessageFile[]>(`${slug}.composer.files`, []);
  // a file sent to a bin from here defaults to the project's `uploads` bin: never one named after the note
  const uploadsBin = bins.find((b) => b.name.trim().toLowerCase() === UPLOADS_BIN)?.id;
  const [attached, setAttached] = useState<UploadOut[]>([]);
  const [uploading, setUploading] = useState(0);
  const [toBin, setToBin] = useState<MessageFile | null>(null); // the chip whose bin sheet is open
  const pickRef = useRef<HTMLInputElement>(null);
  const command = useCommand(slug);
  const toast = useToast();
  const addFiles = useCallback(
    (picked: File[]) => {
      for (const file of picked) {
        setUploading((n) => n + 1);
        uploadChatFile(slug, file)
          .then((f) => setFiles((prev) => [...prev, f]))
          .catch((err) => toast(`${file.name}: ${errorText(err)}`))
          .finally(() => setUploading((n) => n - 1));
      }
    },
    [slug, setFiles, toast],
  );
  // through a ref: the drop is taken once, whatever re-renders while it uploads
  const addRef = useRef(addFiles);
  addRef.current = addFiles;
  useEffect(() => {
    if (!dropped) return;
    addRef.current(dropped);
    onDroppedTaken();
  }, [dropped, onDroppedTaken]);
  const send = (e?: FormEvent) => {
    e?.preventDefault();
    const t = text.trim();
    // a picture alone is a note: what it carries is the message
    if ((!t && !files.length && !attached.length) || command.isPending || uploading > 0) return;
    command.mutate(
      {
        name: "post_message",
        body: { text: t, kind: "user", attachments: attached.map((u) => u.media.id), files: files.map((f) => f.id), ...(about ? { context: about } : {}) },
      },
      {
        onSuccess: () => {
          setText("");
          setFiles([]);
          setAttached([]);
        },
        onError: (err) => toast(errorText(err)),
      },
    );
  };
  const dropFile = (f: MessageFile) => {
    setFiles((prev) => prev.filter((p) => p.id !== f.id));
    void removeChatFile(slug, f.id);
  };
  // the chip's file is project media now: it rides the note as that media, and the note's copy goes
  const kept = (results: UploadOut[]) => {
    if (toBin) dropFile(toBin);
    setToBin(null);
    setAttached((prev) => [...prev, ...results.filter((r) => !prev.some((p) => p.media.id === r.media.id))]);
  };
  const chips = files.length + attached.length + uploading;
  return (
    <>
      <form className="composer" onSubmit={send}>
        {chips > 0 && (
          <div className="composer-chips" aria-label="On this note">
            {files.map((f) => (
              <span key={f.id} className="composer-chip">
                <FileTile slug={slug} file={f} size={40} />
                <button type="button" className="composer-chip-remove" aria-label="Take off this note" title="take off this note" onClick={() => dropFile(f)}>
                  ✕
                </button>
                <button
                  type="button"
                  className="composer-chip-bin"
                  title="on this note only — click to also add it to a bin in the project"
                  aria-label={`Add ${f.name} to a bin`}
                  onClick={() => setToBin(f)}
                >
                  bin
                </button>
              </span>
            ))}
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
            {uploading > 0 && <span className="composer-chip-busy" aria-label="Uploading" title="uploading…" />}
          </div>
        )}
        <div className="composer-row">
          <button type="button" className="composer-attach" onClick={() => pickRef.current?.click()} title="attach a picture or clip to this note (or paste / drop one) — it stays on the note, not in a bin" aria-label="Attach a file to this note">
            +
          </button>
          <input
            ref={pickRef}
            type="file"
            accept="image/*,video/*,audio/*"
            multiple
            style={{ display: "none" }}
            onChange={(e) => {
              addFiles(Array.from(e.target.files ?? []));
              e.target.value = ""; // the same file can be picked again
            }}
          />
          <AutoTextarea
            className="composer-input"
            value={text}
            onChange={setText}
            minRows={1}
            placeholder={chips ? "say what these are for…" : about ? `note about ${about.bin_name || about.sequence_name}…` : "note to the agent…"}
            aria-label="Note to the agent"
            onSubmitKey={send}
          />
          <button type="submit" className="composer-send" disabled={(!text.trim() && !files.length && !attached.length) || command.isPending || uploading > 0} title="send (Enter · Shift+Enter for a new line)">
            Send
          </button>
        </div>
      </form>
      {toBin && <UploadSheet slug={slug} bins={bins} defaultBin={uploadsBin} defaultName={UPLOADS_BIN} keep={toBin} onDone={kept} onClose={() => setToBin(null)} />}
    </>
  );
}
