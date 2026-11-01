import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { errorText, useDecide, type DecisionAction } from "../api/client";
import type { CommandResultOut, FeedEntry, ShotDecision } from "../api/types";
import { Bubble, FeedDivider, isSessionBoundary } from "../components/Bubble";
import { ChangeCard } from "../components/ChangeCard";
import { MediaThumb, ratioOf } from "../components/MediaThumb";
import { ShotsCard } from "../components/ShotsCard";
import { useToast } from "../components/Toast";

interface Props {
  slug: string;
  entries: FeedEntry[];
  /** A session is waiting or live: the hint about approving applies. */
  active?: boolean;
}

/** Prototype toast strings for decisions, with the two design-only outcomes added. */
function decisionToast(action: DecisionAction, note: string, result: CommandResultOut): string {
  const status = result.change?.decision?.status;
  const noted = note.trim().length > 0;
  if (action === "approve") return noted ? "approved — note sent to agent" : "approved — agent continues";
  if (action === "close") return "closed — nothing sent";
  if (action === "reject") {
    if (status === "superseded") return "rejected — superseded by a newer change, agent notified";
    return noted ? "rejected — undone, note sent to agent" : "rejected — undone, agent notified";
  }
  return status === "pending" ? "reopened — change re-applied, agent notified" : "reopened — agent notified";
}

type Who = "you" | "agent";

/**
 * A line that is an action, not a message, and owes no decision: the user's own UI edit
 * (`you: <description>`) or the agent's auto-mode change (`agent: <description> -- <reason>`).
 * The server posts both as `system` lines with the change attached. Consecutive ones digest.
 */
function actionWho(e: FeedEntry): Who | null {
  const kind = e.message.kind;
  // the batch's own line ahead of its shots cards names no change
  if (!e.change && kind === "system" && /^agent:\s/i.test(e.message.text)) return "agent";
  if (!e.change || e.change.decision || isSessionBoundary(e)) return null;
  if (e.change.actor === "user") return kind === "system" || kind === "change" ? "you" : null;
  // an agent `change` line without a decision is a card for a command the user asked for: it stays a card
  return kind === "system" ? "agent" : null;
}

/** Description and reason for a digest line; the change carries them, the text is the fallback. */
function actionText(e: FeedEntry): { what: string; why: string | null } {
  if (e.change?.description) return { what: e.change.description, why: e.change.reason?.trim() || null };
  const [what, ...rest] = e.message.text.replace(/^(you|agent):\s*/i, "").split(" -- ");
  return { what, why: rest.join(" -- ").trim() || null };
}

type Group =
  | { kind: "one"; entry: FeedEntry }
  | { kind: "digest"; who: Who; key: string; entries: FeedEntry[] }
  | { kind: "batch"; entry: FeedEntry; children: Group[]; renders: FeedEntry[] };

/** The shots card: a propose_shots change, whatever line carries it. */
function isBatch(e: FeedEntry): boolean {
  return !!e.change && e.change.command === "propose_shots" && (e.message.kind === "change" || e.message.kind === "system");
}

/**
 * Consecutive actions by the same hand collapse into one digest row; everything else stands
 * alone. A shots card gathers its renders: every line about a change tagged with its id (the
 * queued renders, their takes landing as cards, the decisions on those) folds under the card,
 * grouped the same way, so a batch is one row in the feed however many renders it runs.
 */
function groupEntries(entries: FeedEntry[], nested = false): Group[] {
  const batches = nested ? new Set<string>() : new Set(entries.filter(isBatch).map((e) => e.change!.id));
  const children = new Map<string, FeedEntry[]>();
  const top: FeedEntry[] = [];
  for (const entry of entries) {
    const batch = entry.change?.batch;
    if (batch && batches.has(batch)) {
      const list = children.get(batch) ?? [];
      list.push(entry);
      children.set(batch, list);
    } else {
      top.push(entry);
    }
  }
  const out: Group[] = [];
  for (const entry of top) {
    const last = out[out.length - 1];
    const who = actionWho(entry);
    if (!nested && isBatch(entry)) {
      const renders = children.get(entry.change!.id) ?? [];
      out.push({ kind: "batch", entry, children: groupEntries(renders, true), renders });
    } else if (who) {
      if (last && last.kind === "digest" && last.who === who) last.entries.push(entry);
      else out.push({ kind: "digest", who, key: entry.message.id, entries: [entry] });
    } else {
      out.push({ kind: "one", entry });
    }
  }
  return out;
}

/**
 * The feed body: chronological, newest at the bottom, rendered column-reverse so the
 * scroll pins to the bottom on its own. Change lines become cards; session boundaries become
 * dividers; runs of actions become a digest; everything else is a bubble.
 */
export function Feed({ slug, entries, active }: Props) {
  const navigate = useNavigate();
  const decide = useDecide(slug);
  const toast = useToast();

  // stable callbacks: the cards are memoized, and a fresh closure per render would undo that
  const { mutate } = decide;
  const onDecide = useCallback(
    (id: string, action: DecisionAction, note: string, items?: ShotDecision[]) =>
      mutate(
        { id, action, note, items },
        {
          onSuccess: (result) => {
            const n = result.generations?.length ?? 0;
            const batch =
              result.change?.decision?.status === "closed"
                ? "closed — nothing kept"
                : n === 0
                  ? "approved — bin made, the agent rewrites the prompt before it renders"
                  : `approved — ${n} render${n === 1 ? "" : "s"} queued`;
            toast(items ? batch : decisionToast(action, note, result));
          },
          onError: (e) => toast(errorText(e)),
        },
      ),
    [mutate, toast],
  );
  // pushes the bin on top of the session entry, so back returns to the open session
  const onEdit = useCallback(
    (binId: string) => {
      navigate(`/p/${encodeURIComponent(slug)}/b/${encodeURIComponent(binId)}`);
      toast("manual mode — you override the agent");
    },
    [navigate, slug, toast],
  );
  const onEditSettings = useCallback(() => {
    navigate(`/p/${encodeURIComponent(slug)}/settings`);
    toast("manual mode — you override the agent");
  }, [navigate, slug, toast]);

  const groups = useMemo(() => groupEntries(entries).reverse(), [entries]);
  const render = (g: Group): JSX.Element =>
    g.kind === "batch" ? (
      <Batch key={g.entry.message.id} slug={slug} group={g} render={render} onDecide={onDecide} onEdit={onEdit} busy={decide.isPending} />
    ) : g.kind === "digest" ? (
      <Digest key={g.key} slug={slug} who={g.who} entries={g.entries} />
    ) : isSessionBoundary(g.entry) ? (
      <FeedDivider key={g.entry.message.id} entry={g.entry} />
    ) : g.entry.message.kind === "change" && g.entry.change ? (
      <ChangeCard
        key={g.entry.message.id}
        slug={slug}
        entry={g.entry}
        onDecide={onDecide}
        onEdit={onEdit}
        onEditSettings={onEditSettings}
        busy={decide.isPending}
      />
    ) : (
      <Bubble key={g.entry.message.id} entry={g.entry} />
    );
  return (
    <div className="agent-feed">
      {groups.map(render)}
      {active && <div className="hint is-center feed-hint">changes apply live — approve to keep, or write a note and send it back to the driving agent</div>}
    </div>
  );
}

/**
 * A shots card with its renders folded under it: one row for the batch, then a digest line
 * counting the renders, the takes that landed and the verdicts still owed, with `⌄ show` for
 * the render cards themselves (grouped like the feed: cards where the mode asks, digest lines
 * where it does not). Collapsed by default: the batch is one row however many renders it ran.
 */
function Batch({
  slug,
  group,
  render,
  onDecide,
  onEdit,
  busy,
}: {
  slug: string;
  group: Extract<Group, { kind: "batch" }>;
  render: (g: Group) => JSX.Element;
  onDecide: (id: string, action: DecisionAction, note: string, items?: ShotDecision[]) => void;
  onEdit: (binId: string) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const renders = new Map<string, FeedEntry>();
  for (const e of group.renders) if (e.change) renders.set(e.change.id, e); // the last line about a render carries its latest change
  const all = [...renders.values()];
  const n = all.length;
  const landed = all.filter((e) => !!e.media).length;
  const owed = all.filter((e) => e.change?.decision?.status === "pending" || e.change?.decision?.status === "proposed").length;
  return (
    <div className="batch">
      <ShotsCard slug={slug} entry={group.entry} onDecide={onDecide} onEdit={onEdit} busy={busy} />
      {n > 0 && (
        <div className={`edits-digest is-agent batch-digest ${owed ? "is-owed" : ""}`}>
          <div className="edits-digest-row">
            <span>
              <span className="edits-digest-you">agent</span> · {n} {n === 1 ? "render" : "renders"}
              {landed ? ` · ${landed} landed` : ""}
              {owed > 0 && <span className="batch-owed"> · {owed} need{owed === 1 ? "s" : ""} a verdict</span>}
            </span>
            <button type="button" className="edits-digest-show" onClick={() => setOpen(!open)} aria-expanded={open}>
              {open ? "⌃ hide" : "⌄ show"}
            </button>
          </div>
          {open && <div className="batch-children">{group.children.map(render)}</div>}
        </div>
      )}
    </div>
  );
}

/**
 * A run of actions as a ticker: the newest one shows in full in a fixed slot, and every one
 * before it folds into the header count -- `agent · 22 earlier changes across 10 bins`, with
 * `⌄ show` for the folded lines. As a batch streams in each new line takes the slot and the
 * previous one joins the count, so the user watches what is changing go by in one place.
 * The agent still reads every event.
 */
function Digest({ slug, who, entries }: { slug: string; who: Who; entries: FeedEntry[] }) {
  const [open, setOpen] = useState(false);
  const latest = entries[entries.length - 1];
  const earlier = entries.slice(0, -1).reverse();
  const n = earlier.length;
  const bins = [...new Set(entries.map((e) => e.bin?.name).filter((x): x is string => !!x))];
  const many = bins.length > 1;
  const noun = who === "you" ? (n === 1 ? "edit" : "edits") : n === 1 ? "change" : "changes";
  const where = bins.length === 1 ? ` in ${bins[0]}` : many ? ` across ${bins.length} bins` : "";
  return (
    <div className={`edits-digest ${who === "agent" ? "is-agent" : ""}`}>
      <div className="edits-digest-row">
        <span>
          <span className="edits-digest-you">{who}</span>
          {n > 0 ? ` · ${n} earlier ${noun}${where}` : where ? ` ·${where}` : ""}
        </span>
        {n > 0 && (
          <button type="button" className="edits-digest-show" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "⌃ hide" : "⌄ show"}
          </button>
        )}
      </div>
      {/* keyed by the message so each replacement re-runs the fade and reads at speed */}
      <DigestLine key={latest.message.id} slug={slug} entry={latest} showBin={many} latest />
      {open && earlier.map((e) => <DigestLine key={e.message.id} slug={slug} entry={e} showBin={many} />)}
    </div>
  );
}

function DigestLine({ slug, entry, showBin, latest }: { slug: string; entry: FeedEntry; showBin: boolean; latest?: boolean }) {
  const { what, why } = actionText(entry);
  const media = entry.media;
  return (
    <div className={`edits-digest-line ${latest ? "edits-digest-latest" : ""}`}>
      {media && (
        <span className="edits-digest-thumb">
          <MediaThumb slug={slug} id={media.id} type={media.type} duration={media.duration_s} size={40} ratio={ratioOf(media)} />
        </span>
      )}
      <span className="edits-digest-text">
        <span>
          {entry.bin && showBin && <span className="edits-digest-you">{entry.bin.name} </span>}
          {what}
        </span>
        {why && <span className="edits-digest-reason">{why}</span>}
      </span>
    </div>
  );
}
