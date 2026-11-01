import { memo, useEffect } from "react";
import { thumbUrl, useCancelJob, useLiveJobs, type DecisionAction } from "../api/client";
import type { DecisionStatus, FeedEntry } from "../api/types";
import { useMediaViewer } from "../layout/useMediaViewer";
import { AutoTextarea } from "./AutoTextarea";
import { CardReason, changeAsk } from "./cardQuestion";
import { JobStatusLine } from "./JobProgress";
import { NoteFilesArea, SentFiles, useNoteFiles } from "./NoteFiles";
import { AudioBadge, VideoBadge } from "./MediaThumb";
import { useToast } from "./Toast";
import { useDraft } from "./useDraft";

interface Props {
  slug: string;
  entry: FeedEntry;
  onDecide: (id: string, action: DecisionAction, note: string, items?: undefined, hold?: undefined, files?: string[]) => void;
  onEdit: (binId: string) => void;
  onEditSettings?: () => void;
  busy?: boolean;
}

const LABELS: Record<DecisionStatus, string> = {
  pending: "PENDING",
  proposed: "PROPOSED",
  approved: "APPROVED ✓",
  rejected: "REJECTED ✕",
  superseded: "SUPERSEDED",
  closed: "CLOSED",
  replied: "REPLIED",
};

/**
 * Prototype change card: CHANGE · status · close / reopen, media preview when the change
 * produced or bound media, the derived description, the agent's reason for it, "edit <bin>
 * manually →", a note while a decision is owed, the sent note afterwards.
 *
 * An open card leads with the question it asks (`cardQuestion`), and the button answers it:
 * a take reads "Approve" (it is done: no further generation needed on it -- the one thing that
 * word is kept for), a change already applied "Keep", any other proposal "Yes"; all land
 * `approve`. A proposal, which has not run, also offers "No" (`close`).
 * Typing turns the button into "send" and lands `reply`: the note is the instruction, nothing ran and
 * nothing was undone, the agent reads it and acts (redoes the take, revises the card, reverses
 * its change). A REPLIED card keeps a bare `approve` link for later. `close` in the head is the
 * third way out: seen, nothing to say, nothing owed.
 *
 * A generate change is a card from the moment it queues in a review mode; until its take lands
 * the card reads as rendering (the live job's phase, a cancel), never as something to approve.
 *
 * The note is a draft that survives navigation and reloads (`useDraft`), keyed on the change.
 * Memoized: a feed refetch keeps the identity of entries that did not change, so only the
 * card that moved renders again. The feed passes stable callbacks for the same reason.
 */
export const ChangeCard = memo(ChangeCardBody);

const LIVE = new Set(["queued", "running", "held"]);

function ChangeCardBody({ slug, entry, onDecide, onEdit, onEditSettings, busy }: Props) {
  const change = entry.change!;
  // No decision at all means nobody owes one: the user's own edits, and agent commands the
  // user asked for in the feed. Only a change carrying a decision can be open for a verdict.
  const owed = !!change.decision;
  const status: DecisionStatus = change.decision?.status ?? "pending";
  const media = entry.media;
  const render = entry.render;
  // the render this change started, still on its way: not a verdict yet
  const rendering = change.command === "generate" && !media && !!render && LIVE.has(render.status);
  const renderLost = change.command === "generate" && !media && !!render && (render.status === "failed" || render.status === "cancelled");
  const landing = change.command === "generate" && !media && !!render && render.status === "done";
  const open = owed && (status === "pending" || status === "proposed") && !rendering && !renderLost && !landing;
  const replied = owed && status === "replied";
  const canReopen = status === "rejected" || status === "superseded" || status === "closed";
  const [note, setNote] = useDraft(`${slug}.${change.id}`, "");
  // a reopened card gets its old note back in the input, as the prototype does
  useEffect(() => {
    if (open && !note && change.decision?.note) setNote(change.decision.note);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);
  const sentNote = !open ? change.decision?.note : null;
  const sentFiles = !open ? change.decision?.files : null;
  const attach = useNoteFiles(slug, `${slug}.${change.id}`, open);
  // words or files: either makes the button a send, and the verdict a reply
  const saying = !!note.trim() || attach.any;
  const name = media ? media.path.split("/").pop() : null;
  const viewer = useMediaViewer();
  const jobs = useLiveJobs(rendering ? slug : undefined);
  const job = rendering ? (jobs.data ?? []).find((j) => j.job === render!.generation) : undefined;
  const cancel = useCancelJob(slug);
  const toast = useToast();
  const decide = (action: DecisionAction) => {
    if (busy) return;
    if (action === "reply" && attach.uploading > 0) return;
    setNote("");
    const files = action === "reply" ? attach.ids : undefined;
    onDecide(change.id, action, note, undefined, undefined, files);
  };
  const submit = () => (saying ? decide("reply") : ask.action === "Close" ? onDecide(change.id, "close", "") : decide("approve"));
  // the question and the button's word: Approve only for a take that is done
  const ask = changeAsk(entry);
  const label = status === "approved" && ask.action !== "Approve" ? "OK ✓" : LABELS[status];
  const cancelRender = () => {
    if (busy || cancel.isPending) return;
    cancel.mutate(render!.generation, {
      onSuccess: () => onDecide(change.id, "close", ""),
      onError: () => toast("could not cancel — the generation may have finished; the card follows it"),
    });
  };

  return (
    <div id={`change-${change.id}`} className={`change-card is-${status} ${rendering ? "is-rendering" : ""}`}>
      <div className="row change-head">
        <span className="tag">CHANGE</span>
        <span className="spacer" />
        <span className="change-status">{rendering ? "GENERATING" : owed ? label : "DONE"}</span>
        {replied && ask.action !== "Close" && (
          <button type="button" className="link" onClick={() => decide("approve")} disabled={busy} title={ask.hint}>
            {ask.action.toLowerCase()}
          </button>
        )}
        {((open && status !== "proposed") || replied || (renderLost && (status === "pending" || status === "proposed"))) && (
          <button type="button" className="link change-close" onClick={() => onDecide(change.id, "close", "")} disabled={busy} title="seen — nothing owed, nothing sent">
            close
          </button>
        )}
        {canReopen && (
          <button type="button" className="link" onClick={() => onDecide(change.id, "reopen", note)} disabled={busy}>
            reopen
          </button>
        )}
      </div>
      {media && (
        <div
          className="change-preview is-tappable"
          role="button"
          tabIndex={0}
          title="open full size"
          onClick={() => viewer.open(slug, media.id)}
          onKeyDown={(e) => {
            if (e.key === "Enter") viewer.open(slug, media.id);
          }}
        >
          <img src={thumbUrl(slug, media.id, media.type === "video" ? "poster" : "detail")} alt="" loading="lazy" />
          {media.type === "video" && <VideoBadge duration={media.duration_s} />}
          {media.type === "audio" && <AudioBadge duration={media.duration_s} />}
          {name && <span className="change-preview-name">{name}</span>}
          {media.qc?.status === "fail" && <span className="change-preview-qc">fail</span>}
        </div>
      )}
      {rendering && (
        <div className="row change-rendering" aria-live="polite">
          <span className="change-rendering-dot" aria-hidden="true" />
          <span className="change-rendering-text">
            <JobStatusLine job={job ?? { status: render!.status === "held" ? "queued" : render!.status, task: null, phase: null, step: null, steps: null, pass_no: null, passes: null, queue_ahead: null, queue_ahead_ours: null }} />
          </span>
          <button type="button" className="link" onClick={cancelRender} disabled={busy || cancel.isPending} title="stop this generation; the card closes">
            cancel
          </button>
        </div>
      )}
      {renderLost && <div className="change-rendering-lost">{render!.status === "failed" ? "render failed — nothing landed" : "render cancelled"}</div>}
      {landing && <div className="change-rendering-lost">landing…</div>}
      {open && <div className="change-question">{ask.question}</div>}
      <div className={`change-desc ${open ? "is-detail" : ""}`}>{change.description.replace(/^proposed:\s*/i, "")}</div>
      {change.reason && <CardReason text={change.reason} />}
      {entry.bin && (
        <button type="button" className="link change-edit" onClick={() => onEdit(entry.bin!.id)}>
          edit {entry.bin.name} manually →
        </button>
      )}
      {!entry.bin && change.command === "set_project_settings" && onEditSettings && (
        <button type="button" className="link change-edit" onClick={onEditSettings}>
          edit settings manually →
        </button>
      )}
      {open && (
        <NoteFilesArea slug={slug} note={attach}>
          <AutoTextarea
            className="change-note"
            placeholder="or say what to change instead…"
            value={note}
            onChange={setNote}
            aria-label="Note"
            onSubmitKey={submit}
          />
        </NoteFilesArea>
      )}
      {sentNote &&
        // a close carries no note from you: one on a closed card is why it closed (the agent
        // failed the take, the generation was cancelled), not something you sent
        (status === "closed" ? <div className="change-sent">closed — {sentNote}</div> : <div className="change-sent">“{sentNote}” — sent to agent</div>)}
      {!sentNote && sentFiles?.length ? <div className="change-sent">sent to agent</div> : null}
      <SentFiles slug={slug} files={sentFiles} />
      {open && (
        <div className="row change-actions">
          <button type="button" className="btn-approve is-yes" disabled={busy || attach.uploading > 0} onClick={submit} title={saying ? "Enter to send — a note is a reply, not an approval" : ask.hint}>
            {saying ? "send →" : ask.action}
          </button>
          {/* a proposal has not run: No leaves it that way. A change already applied has no No --
              say what to change, or close the card */}
          {status === "proposed" && !saying && (
            <button type="button" className="btn-approve" disabled={busy} onClick={() => onDecide(change.id, "close", "")} title="no: nothing runs, the card closes">
              No
            </button>
          )}
        </div>
      )}
    </div>
  );
}
