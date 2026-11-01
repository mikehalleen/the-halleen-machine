import { memo } from "react";
import type { FeedEntry } from "../api/types";

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

function BubbleLine({ entry }: { entry: FeedEntry }) {
  const m = entry.message;
  switch (m.kind) {
    case "user":
      return (
        <div className="bubble bubble-user">
          {m.text}
          {m.context && (
            <span className="bubble-meta">
              re: {m.context.bin_name || m.context.bin}
              {m.context.media_label ? ` · ${m.context.media_label}` : ""}
            </span>
          )}
          {m.attachments && m.attachments.length > 0 && (
            <span className="bubble-meta">
              {m.attachments.length} reference file{m.attachments.length === 1 ? "" : "s"} attached
            </span>
          )}
        </div>
      );
    case "agent":
      return <div className="bubble bubble-agent">{m.text}</div>;
    case "blocker":
      return (
        <div className="bubble bubble-agent is-blocker">
          <span className="bubble-kind">NEEDS YOU</span>
          {m.text}
        </div>
      );
    case "decision": {
      const what = entry.change?.description ?? "a change";
      const verb = m.status === "pending" || m.status === "proposed" ? "reopened" : (m.status ?? "decided");
      return (
        <div className="bubble-sys">
          you {verb}: {what}
          {m.note ? ` — “${m.note}”` : ""}
        </div>
      );
    }
    default:
      return <div className="bubble-sys">{m.text}</div>;
  }
}
