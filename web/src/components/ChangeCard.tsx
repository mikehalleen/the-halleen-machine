import { memo, useEffect, useState } from "react";
import { thumbUrl, type DecisionAction } from "../api/client";
import type { DecisionStatus, FeedEntry } from "../api/types";
import { useMediaViewer } from "../layout/useMediaViewer";
import { AutoTextarea } from "./AutoTextarea";
import { VideoBadge } from "./MediaThumb";

interface Props {
  slug: string;
  entry: FeedEntry;
  onDecide: (id: string, action: DecisionAction, note: string) => void;
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
};

/**
 * Prototype change card: CHANGE · status · reopen, media preview when the change produced or
 * bound media, the derived description, the agent's reason for it, "edit <bin> manually →", a
 * note while a decision is owed, the sent note afterwards. PROPOSED and SUPERSEDED come from
 * the design.
 *
 * One button, not two. Empty note reads "approve"; typing turns it into "send". Both land the
 * same `approve` decision with the note attached — a reply is not a rejection, it is direction,
 * and the agent reads it and acts (including reverting its own change when that is what the
 * note means). Reject still exists in the decision API for the agent and for reopen; the user
 * simply never has to choose a verdict when what they have is a sentence. `close` in the head
 * is the third way out: seen, nothing to say, nothing owed — for the card that only wanted
 * a look ("pick one of these"), so "no action" never has to be typed.
 *
 * Memoized: a feed refetch keeps the identity of entries that did not change, so only the
 * card that moved renders again. The feed passes stable callbacks for the same reason.
 */
export const ChangeCard = memo(ChangeCardBody);

function ChangeCardBody({ slug, entry, onDecide, onEdit, onEditSettings, busy }: Props) {
  const change = entry.change!;
  // No decision at all means nobody owes one: the user's own edits, and agent commands the
  // user asked for in the feed. Only a change carrying a decision can be open for a verdict.
  const owed = !!change.decision;
  const status: DecisionStatus = change.decision?.status ?? "pending";
  const open = owed && (status === "pending" || status === "proposed");
  const canReopen = status === "rejected" || status === "superseded" || status === "closed";
  const [note, setNote] = useState("");
  // a reopened card gets its old note back in the input, as the prototype does
  useEffect(() => {
    if (open && !note && change.decision?.note) setNote(change.decision.note);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);
  const sentNote = !open ? change.decision?.note : null;
  const media = entry.media;
  const name = media ? media.path.split("/").pop() : null;
  const viewer = useMediaViewer();

  return (
    <div className={`change-card is-${status}`}>
      <div className="row change-head">
        <span className="tag">CHANGE</span>
        <span className="spacer" />
        <span className="change-status">{owed ? LABELS[status] : "DONE"}</span>
        {open && (
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
          {name && <span className="change-preview-name">{name}</span>}
          {media.qc?.status === "fail" && <span className="change-preview-qc">fail</span>}
        </div>
      )}
      <div className="change-desc">{change.description}</div>
      {change.reason && <div className="change-reason">{change.reason}</div>}
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
        <AutoTextarea
          className="change-note"
          placeholder="reply, redirect, ask — or just approve…"
          value={note}
          onChange={setNote}
          aria-label="Note"
          onSubmitKey={() => !busy && onDecide(change.id, "approve", note)}
        />
      )}
      {sentNote && <div className="change-sent">“{sentNote}” — sent to agent</div>}
      {open && (
        <div className="row change-actions">
          <button type="button" className="btn-approve" disabled={busy} onClick={() => onDecide(change.id, "approve", note)} title="Enter to send">
            {note.trim() ? "send →" : "✓ approve"}
          </button>
        </div>
      )}
    </div>
  );
}
