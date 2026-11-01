import { memo } from "react";
import { Link } from "react-router-dom";
import { useProject } from "../api/client";
import type { FeedEntry } from "../api/types";
import { useMediaViewer } from "../layout/useMediaViewer";
import { MediaThumb } from "./MediaThumb";
import { MessageFiles } from "./MessageFiles";

const BOUNDARIES = new Set(["start_session", "connect_session", "end_session"]);

/** A session boundary (started, connected, ended): the feed draws it as a divider, not a line. */
export function isSessionBoundary(entry: FeedEntry): boolean {
  return !!entry.change && BOUNDARIES.has(entry.change.command);
}

function clock(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** `14:02 · connected`: centered mono, rules filling each side. */
export function FeedDivider({ entry }: { entry: FeedEntry }) {
  const time = clock(entry.message.ts);
  const text = entry.message.text || entry.change?.description || "";
  return (
    <div className="feed-divider">
      {time ? `${time} · ${text}` : text}
    </div>
  );
}

/** One feed line that is not a change card: user, agent, blocker, system, or decision.
 * Memoized: a feed refetch keeps the identity of entries that did not change, so only the
 * lines that moved render again. */
export const Bubble = memo(BubbleLine);

function BubbleLine({ slug, entry }: { slug: string; entry: FeedEntry }) {
  const m = entry.message;
  const viewer = useMediaViewer();
  const project = useProject(m.attachments && m.attachments.length > 0 ? slug : undefined);
  // the bin a take lives in, so a picture in the chat leads back to where it was made
  const binOf = (id: string) => project.data?.bins.find((b) => (b.media ?? []).includes(id));
  // what the agent attached is the point of the line: the take it means, full size on tap
  const attachments =
    m.attachments && m.attachments.length > 0 ? (
      <span className="row wrap bubble-attachments" aria-label="Attached media">
        {m.attachments.map((id) => {
          const bin = binOf(id);
          return (
            <span key={id} className="bubble-attachment">
              <MediaThumb slug={slug} id={id} size={72} title="open full size" onClick={() => viewer.open(slug, id)} />
              {bin && (
                <Link className="bubble-attachment-bin" to={`/p/${encodeURIComponent(slug)}/b/${encodeURIComponent(bin.id)}`} title={`open ${bin.name}`}>
                  {bin.name}
                </Link>
              )}
            </span>
          );
        })}
      </span>
    ) : null;
  // the note's own files (a pasted screenshot): in no bin, shown where they were said
  const files = m.files && m.files.length > 0 ? <MessageFiles slug={slug} files={m.files} /> : null;
  switch (m.kind) {
    case "user": {
      const note = (
        <div className="bubble bubble-user">
          {m.text}
          {files}
          {m.context && (
            <span className="bubble-meta">
              re: {[m.context.sequence_name, m.context.bin_name ? `${m.context.position ? `#${m.context.position} ` : ""}${m.context.bin_name}` : m.context.bin, m.context.media_label].filter(Boolean).join(" · ")}
            </span>
          )}
          {m.attachments && m.attachments.length > 0 && (
            <span className="bubble-meta">
              {m.attachments.length} reference file{m.attachments.length === 1 ? "" : "s"} attached
            </span>
          )}
          {/* a note still owed: the agent cannot go idle while it is open */}
          {!entry.answer && entry.open && (
            <span className="bubble-meta bubble-open" title="the agent has not dealt with this note yet — it cannot go idle while it is open">
              ○ open
            </span>
          )}
        </div>
      );
      if (!entry.answer) return note;
      // what the agent did about the note reads as its reply, a chat bubble right under it
      // (one block: the feed runs bottom-up, and the two must keep their order)
      return (
        <div className="bubble-pair">
          {note}
          <div className="bubble bubble-agent bubble-reply" title="what the agent did about this note">
            {entry.answer}
          </div>
        </div>
      );
    }
    case "agent":
      return (
        <div className="bubble bubble-agent">
          {m.text}
          {attachments}
          {files}
        </div>
      );
    case "blocker":
      return (
        <div className="bubble bubble-agent is-blocker">
          <span className="bubble-kind">NEEDS YOU</span>
          {m.text}
          {attachments}
          {files}
        </div>
      );
    case "decision": {
      const what = entry.change?.description ?? "a change";
      const verb = m.status === "pending" || m.status === "proposed" ? "reopened" : (m.status ?? "decided");
      return (
        <div className="bubble-sys">
          you {verb}: {what}
          {m.note ? ` — “${m.note}”` : ""}
          {files}
        </div>
      );
    }
    default: {
      // a mode switch: the mode, then a line on what it means ("session mode manual -- every render ...")
      if (entry.change?.command === "set_session_mode") {
        const [head, ...rest] = m.text.split(" -- ");
        return (
          <div className="bubble-sys bubble-mode">
            <span className="bubble-mode-head">{head.replace(/^session mode /, "mode: ")}</span>
            {rest.map((line, i) => (
              <span key={i} className="bubble-mode-meaning">
                {line}
              </span>
            ))}
          </div>
        );
      }
      return <div className="bubble-sys">{m.text}</div>;
    }
  }
}
