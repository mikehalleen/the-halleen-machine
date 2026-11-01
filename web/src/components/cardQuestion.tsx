import { useState } from "react";
import type { FeedEntry, ShotOut } from "../api/types";

/**
 * What a card asks, and the word on the button that answers it. A card that owes a verdict
 * leads with one plain sentence saying what the click does, and the button names that act:
 *
 * - a shot card greenlights a render, so its button says **Generate**: never "approve", which
 *   reads as agreeing with the description rather than spending the render;
 * - **Approve** is kept for one thing: a take that is done, no further generation needed on it;
 * - a change already applied is **Keep**; any other proposal is **Yes**.
 *
 * The description and the agent's reason are the detail beneath, not the thing to decode.
 */
export interface CardAsk {
  question: string;
  /** The button's word. */
  action: string;
  /** The button's hover: what the click does, in full. */
  hint: string;
}

export function changeAsk(entry: FeedEntry): CardAsk {
  const change = entry.change!;
  const proposed = change.decision?.status === "proposed";
  const noun = entry.media?.type === "video" ? "video" : entry.media?.type === "image" ? "image" : "take";
  const done: CardAsk = { question: `Is this ${noun} done?`, action: "Approve", hint: "approve: this take is done — no further generation needed on it" };
  switch (change.command) {
    case "generate":
      // a take the agent already failed is not up for approval: the card only wants closing
      if (entry.media?.qc?.status === "fail")
        return { question: "The agent marked this take fail — nothing to approve.", action: "Close", hint: "close: seen, nothing sent; the agent's mark stands" };
      return entry.media ? done : { question: "Run this generation?", action: "Generate", hint: "generate: the generation starts" };
    case "mark_take":
      return entry.media ? done : { question: "Do you agree with this mark?", action: "Yes", hint: "yes: the mark stands" };
    case "set_project_settings":
      if (/brief/i.test(change.description))
        return proposed
          ? { question: "Go with this revised brief?", action: "Use this brief", hint: "the brief is replaced with this one; nothing is generated" }
          : { question: "Keep this revised brief?", action: "Keep", hint: "keep: the brief stays as revised" };
      return proposed
        ? { question: "Change the project settings?", action: "Yes", hint: "yes: the settings change" }
        : { question: "Keep these project settings?", action: "Keep", hint: "keep: the settings stay as changed" };
    case "upload_post": {
      // the agent's ask before the one outward step: the video goes to the project's channel,
      // private. The line names the channel (`... to youtube @halleen (private)`), so the card does too
      const handle = /\bto youtube (@\S+)/.exec(change.description)?.[1];
      const where = handle ? `YouTube ${handle}` : "YouTube";
      return proposed
        ? { question: `Upload this video to ${where} as private?`, action: "Upload", hint: `upload: the video goes to ${where} as private; you finish and publish it in YouTube Studio` }
        : { question: "Keep this upload?", action: "Keep", hint: "keep: the upload stands" };
    }
    case "delete_bin":
    case "delete_media": {
      const what = change.command === "delete_bin" ? "bin" : "media";
      return proposed
        ? { question: `Delete this ${what}?`, action: "Delete", hint: `delete: the ${what} goes` }
        : { question: `Keep this ${what} deleted?`, action: "Keep", hint: "keep: it stays deleted" };
    }
    default:
      return proposed
        ? { question: "Make this change?", action: "Yes", hint: "yes: the change is made" }
        : { question: "Keep this change?", action: "Keep", hint: "keep: the change stays; nothing is generated" };
  }
}

/** One shot: the click generates (or regenerates) it, and the question says so. */
export function shotQuestion(s: ShotOut): string {
  const verb = s.takes > 0 ? "Regenerate" : "Generate";
  const noun = s.kind === "video" ? "video" : s.kind === "image" ? "image" : "shot";
  if (s.full_quality) return `${verb} this ${noun} at full quality?`;
  if (s.kind === "video" && s.quarter) return `${verb} this ${noun} as a quarter-size draft?`;
  return `${verb} this ${noun}?`;
}

/** A batch on one card (an older record): the count stands in for the names. */
export function shotsQuestion(shots: ShotOut[]): string {
  if (shots.length === 1) return shotQuestion(shots[0]);
  return `Generate these ${shots.length} shots?`;
}

/** The agent's reason, two lines at rest: the question is the card, the why is there on a tap. */
export function CardReason({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className={`change-reason ${open ? "" : "is-clamped"}`}
      onClick={() => setOpen((o) => !o)}
      title={open ? "tap to shorten" : "tap to read all of it"}
    >
      {text}
    </div>
  );
}
