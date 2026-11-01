import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { errorText, useDecide, type DecisionAction } from "../api/client";
import type { CommandResultOut, FeedEntry, ShotDecision } from "../api/types";
import { Bubble, FeedDivider, isSessionBoundary } from "../components/Bubble";
import { ChangeCard } from "../components/ChangeCard";
import { MediaThumb, ratioOf } from "../components/MediaThumb";
import { ShotsCard } from "../components/ShotsCard";
import { useToast } from "../components/Toast";
import { useJumpRequests } from "./jumpCard";

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
  if (action === "approve") return noted ? "sent — the agent reads your note" : "done — agent continues";
  if (action === "reply") return "sent — the agent reads your note";
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
  // a mode switch stands on its own line with what the mode means: never folded into the edits
  if (e.change.command === "set_session_mode") return null;
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

/** How many of the newest groups stay unfolded in a long feed. */
const RECENT = 20;

/** A card still waiting on the user: pending (applied, owes a verdict) or proposed (not run).
 * A render that has not landed its take yet is not one: while it is queued, sampling, lost or
 * landing there is nothing to answer, exactly as ChangeCard draws it (no button, only cancel). */
export function isOpenCard(e: FeedEntry): boolean {
  const s = e.change?.decision?.status;
  if (!(s === "pending" || s === "proposed") || !(e.message.kind === "change" || e.message.kind === "system")) return false;
  if (e.change?.command === "generate" && !e.media && e.render) return false;
  return true;
}

/** An open card about `binId`: a change on that bin, or a shot card that renders into it. */
export function openCardAbout(e: FeedEntry, binId: string | undefined): boolean {
  if (!binId || !isOpenCard(e)) return false;
  return e.bin?.id === binId || (e.shots ?? []).some((s) => s.bin?.id === binId);
}

function groupHasChange(g: Group, id: string): boolean {
  if (g.kind === "one") return g.entry.change?.id === id;
  if (g.kind === "digest") return g.entries.some((e) => e.change?.id === id);
  return g.entry.change?.id === id || g.renders.some((e) => e.change?.id === id);
}

type Group =
  | { kind: "one"; entry: FeedEntry }
  | { kind: "digest"; who: Who; key: string; entries: FeedEntry[] }
  | { kind: "batch"; entry: FeedEntry; children: Group[]; renders: FeedEntry[] };

/** The shots card: a propose_shots change, whatever line carries it. */
function isBatch(e: FeedEntry): boolean {
  return !!e.change && e.change.command === "propose_shots" && (e.message.kind === "change" || e.message.kind === "system");
}

/** The feed without "you replied: …" lines whose card is in the feed too: the card already
 * shows its status and the note that went to the agent. A decision on a card that is not loaded
 * keeps its line, the only trace of it here. */
function withoutEchoes(entries: FeedEntry[]): FeedEntry[] {
  const cards = new Set(entries.filter((e) => e.message.kind === "change" && e.change).map((e) => e.change!.id));
  return foldReconnects(entries.filter((e) => !(e.message.kind === "decision" && e.change && cards.has(e.change.id))));
}

function isReconnect(e: FeedEntry): boolean {
  return e.change?.command === "connect_session" && /reconnected|rejoined/.test(e.message.text || e.change?.description || "");
}

/** A run of back-to-back "agent reconnected" lines (an agent that kept coming back overnight)
 * is one line: the last, saying how many and since when. */
function foldReconnects(entries: FeedEntry[]): FeedEntry[] {
  const out: FeedEntry[] = [];
  let run: FeedEntry[] = [];
  const flush = () => {
    if (run.length === 1) out.push(run[0]);
    else if (run.length > 1) {
      const last = run[run.length - 1];
      const since = new Date(run[0].message.ts);
      const at = Number.isNaN(since.getTime()) ? "" : ` since ${since.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
      out.push({ ...last, message: { ...last.message, text: `agent reconnected ${run.length} times${at}` } });
    }
    run = [];
  };
  for (const e of entries) {
    if (isReconnect(e)) run.push(e);
    else {
      flush();
      out.push(e);
    }
  }
  flush();
  return out;
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
    (id: string, action: DecisionAction, note: string, items?: ShotDecision[], hold?: boolean, files?: string[]) =>
      mutate(
        { id, action, note, items, hold, files },
        {
          onSuccess: (result) => {
            const n = result.generations?.length ?? 0;
            const batch =
              result.change?.decision?.status === "closed"
                ? "closed — nothing kept"
                : result.change?.decision?.status === "replied"
                  ? "sent — the agent reads your note"
                  : hold && n > 0
                    ? `saved for later — ${n} generation${n === 1 ? "" : "s"} waiting until you start them`
                    : n === 0
                    ? "bin made — the agent rewrites the prompt before it generates"
                    : `generating — ${n} generation${n === 1 ? "" : "s"} queued`;
            toast(items ? batch : decisionToast(action, note || (files?.length ? "files" : ""), result));
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

  // The user's own note lands in view. The feed pins to the bottom only while it is already
  // there: scrolled up even a little (reading back, or a card that grew), a new line arrives
  // below the fold and the note just sent looks lost. A new user message always snaps back, and
  // so does a mode switch: its line says what the new mode means, and that is worth seeing.
  const feedRef = useRef<HTMLDivElement>(null);
  const lastNote = useMemo(() => {
    for (let i = entries.length - 1; i >= 0; i--) {
      const e = entries[i];
      if (e.message.kind === "user" || e.change?.command === "set_session_mode") return e.message.id;
    }
    return null;
  }, [entries]);
  const shownNote = useRef(lastNote);
  useLayoutEffect(() => {
    if (lastNote === shownNote.current) return;
    shownNote.current = lastNote;
    feedRef.current?.scrollTo({ top: 0 }); // column-reverse: 0 is the bottom, the newest line
  }, [lastNote]);

  const groups = useMemo(() => groupEntries(withoutEchoes(entries)).reverse(), [entries]);
  const groupsRef = useRef(groups);
  groupsRef.current = groups;
  // The cards still owed, oldest first: a long feed is navigated by what still needs a verdict,
  // not by scrolling. The pill at the bottom jumps to the oldest; everything older than both
  // that card and the last RECENT groups folds into one "earlier" row, opened on a tap.
  const openIds = useMemo(() => entries.filter(isOpenCard).map((e) => e.change!.id), [entries]);
  const [older, setOlder] = useState(false);
  useEffect(() => setOlder(false), [slug]);
  // groups are newest first; the cut sits after the last RECENT ones and after every open card
  const firstOpen = openIds.length ? groups.findIndex((g) => groupHasChange(g, openIds[0])) : -1;
  const cut = Math.max(RECENT, firstOpen + 1);
  const shownGroups = older || groups.length <= cut + 1 ? groups : groups.slice(0, cut);
  const folded = groups.length - shownGroups.length;
  // a jump asked for from outside (the activity row's "oldest", the bin header's "card waiting"):
  // a card behind the fold is unfolded first, and the scroll follows on the next frame
  // batches a jump had to open: a render card lives under its batch's "show"
  const [forcedBatches, setForcedBatches] = useState<Set<string>>(() => new Set());
  const jump = useCallback(
    (id: string) => {
      const feed = feedRef.current;
      if (!feed) return;
      if (!document.getElementById(`change-${id}`)) {
        setOlder(true);
        const holder = groupsRef.current.find((g) => g.kind === "batch" && g.renders.some((e) => e.change?.id === id));
        if (holder && holder.kind === "batch") setForcedBatches((s) => new Set(s).add(holder.entry.change!.id));
      }
      // the panel may still be sliding in or laying out: try a few times until the card sits
      // where it should, then stop
      const settle = (left: number) => {
        const card = document.getElementById(`change-${id}`);
        if (card) {
          const delta = card.getBoundingClientRect().top - feed.getBoundingClientRect().top - 12;
          if (Math.abs(delta) > 1) feed.scrollTop += delta;
        }
        if (left > 0) setTimeout(() => settle(left - 1), 150);
      };
      settle(4); // synchronously first: a background tab never gets an animation frame
    },
    [],
  );
  useJumpRequests(jump, entries.length > 0);
  const render = (g: Group): JSX.Element =>
    g.kind === "batch" ? (
      <Batch key={g.entry.message.id} slug={slug} group={g} render={render} onDecide={onDecide} onEdit={onEdit} busy={decide.isPending} forceOpen={forcedBatches.has(g.entry.change!.id)} />
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
      <Bubble key={g.entry.message.id} slug={slug} entry={g.entry} />
    );
  return (
    <div className="agent-feed" ref={feedRef}>
      {openIds.length > 0 && (
        <button type="button" className="feed-jump" onClick={() => jump(openIds[0])} title="scroll to the oldest card still waiting on you">
          ↑ {openIds.length === 1 ? "1 card" : `${openIds.length} cards`} · oldest
        </button>
      )}
      {shownGroups.map(render)}
      {folded > 0 && (
        <button type="button" className="feed-older" onClick={() => setOlder(true)} title="show the older lines">
          ⌃ {folded} earlier · show
        </button>
      )}
      {active && <div className="hint is-center feed-hint">Talk to the agent here: ask for anything, or leave a note on what you see. It reads every message and answers in this chat.</div>}
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
  forceOpen,
}: {
  slug: string;
  group: Extract<Group, { kind: "batch" }>;
  /** A jump landed on one of this batch's renders: show them. */
  forceOpen?: boolean;
  render: (g: Group) => JSX.Element;
  onDecide: (id: string, action: DecisionAction, note: string, items?: ShotDecision[], hold?: boolean, files?: string[]) => void;
  onEdit: (binId: string) => void;
  busy: boolean;
}) {
  const [opened, setOpen] = useState(false);
  const open = opened || !!forceOpen;
  const renders = new Map<string, FeedEntry>();
  for (const e of group.renders) if (e.change) renders.set(e.change.id, e); // the last line about a render carries its latest change
  const all = [...renders.values()];
  const n = all.length;
  const landed = all.filter((e) => !!e.media).length;
  // a take owes a verdict once it is in: a render still waiting ("later") or rendering owes nothing yet
  const owed = all.filter((e) => !!e.media && (e.change?.decision?.status === "pending" || e.change?.decision?.status === "proposed")).length;
  const later = all.filter((e) => e.render?.status === "held").length;
  return (
    <div className="batch">
      <ShotsCard slug={slug} entry={group.entry} onDecide={onDecide} onEdit={onEdit} busy={busy} />
      {n > 0 && (
        <div className={`edits-digest is-agent batch-digest ${owed ? "is-owed" : ""}`}>
          <div className="edits-digest-row">
            <span>
              <span className="edits-digest-you">agent</span> · {n} {n === 1 ? "generation" : "generations"}
              {landed ? ` · ${landed} landed` : ""}
              {later > 0 ? ` · ${later} saved for later` : ""}
              {owed > 0 && (
                <span className="batch-owed" title="takes from this batch waiting for your approve or reject — show them below">
                  {" "}
                  · {owed} {owed === 1 ? "take" : "takes"} to approve
                </span>
              )}
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
 * The agent still reads every event. Folded, the lines carry no thumbnails: media the agent
 * delivers comes with its own post in the chat, and this is the audit trail behind it; opened,
 * every line shows its media.
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
        {(n > 0 || !!latest.media) && (
          <button type="button" className="edits-digest-show" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "⌃ hide" : "⌄ show"}
          </button>
        )}
      </div>
      {/* keyed by the message so each replacement re-runs the fade and reads at speed */}
      <DigestLine key={latest.message.id} slug={slug} entry={latest} showBin={many} thumb={open} latest />
      {open && earlier.map((e) => <DigestLine key={e.message.id} slug={slug} entry={e} showBin={many} thumb />)}
    </div>
  );
}

function DigestLine({ slug, entry, showBin, thumb, latest }: { slug: string; entry: FeedEntry; showBin: boolean; thumb: boolean; latest?: boolean }) {
  const { what, why } = actionText(entry);
  const media = thumb ? entry.media : null;
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
