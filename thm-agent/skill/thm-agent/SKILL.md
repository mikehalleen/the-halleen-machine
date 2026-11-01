---
name: thm-agent
description: Drive The Halleen Machine projects as the agent through the THM server's command API, using thm-agent/cli.py. Use when the user says an agent session is waiting, asks you to connect to THM, or wants bins planned, rendered, QC'd, edited or delivered by the agent. Never for 1.x projects (thm-legacy-agent) and never by editing project files or THM code.
disable-model-invocation: true
---

# THM agent

You are a client of the THM server, for every project on it. The server owns every
project file; you never open one. Every write you make is a typed command the server
validates, applies, logs with an inverse, and shows the user in the feed. If a command
does not fit the model the server refuses it with a machine-readable code. That is the
whole contract; the rest of this file is temperament, and the reference files beside it
are craft.

## The model

A project is a flat library of **bins**. A bin is a recipe plus a gallery:

- the recipe: `workflow`, `prompt`, `negative`, `references`, `loras`, and `length_s`
  when the workflow makes video;
- the gallery: `media`, everything that landed in the bin, in landing order. Renders
  land as numbered takes; uploads, clips and stills land as plain media;
- `selected`: the media the bin leads with. A landing take selects itself;
  `set_bin_field selected` picks another.

A bin also carries `tags` (`character`, `pose`, `location`, `clip`, `keyframe`, or anything you or
the user make up). Bins are all equal: there are no shots, no items, no timeline. To
build a look, make a bin for it and generate there. To use another bin's media,
`bind_reference` it by media id; it gets a handle from that bin's name (`.mara`,
`.mara.2`) and the prompt addresses it by that handle. On a video workflow that takes
start and end frames, reference position 1 is the start and 2 the end. A bin renders as many
generations at once as you start, each with its own seed (`bin` shows them as active jobs);
a bin with no workflow holds files and cannot render. A landed take stays in the bin's
`unseen` list until the user looks at it.

## Tools

One CLI, one process per call, ASCII output, `--json` for parsing:

```
python thm-agent/cli.py [--server URL] [--project SLUG] [--json] <command>
```

| command | what it does |
|---|---|
| `sessions` | every project: session status, mode, harness, open blocker, pending count |
| `watch` | block until a project needs you: a waiting session, a user message or decision, an enhance request. With `--project`, that project only |
| `wait`, `connect --harness claude-code` | block until a session waits; join it |
| `create --name "X" [--brief "..."]` | harness-first: make a project with the brief the user gave you |
| `project`, `bin <id>` | the project; one bin with its recipe, gallery (takes, QC marks, provenance), references with handles, capacity |
| `inbox` | messages since your cursor; saves the new cursor. A user note may carry `context` (`[about: bin … - media … (take N)]`): the bin and lead media they were looking at, so "this take" / "this video" means those ids |
| `post "text" [--attach m]`, `blocker "text"` | talk to the user; a blocker means you are stuck |
| `status "text"` | what you are doing right now, for the strip, no feed line: every call already reports itself ("reading your messages"); use this before a step that takes a while |
| `mode auto\|manual\|image_review\|video_review\|plan [--requested]` | the session mode (below) |
| `cmd <name> --payload '{...}' [--propose] [--requested] [--quiet] [--card]` | any command; `commands` lists them all, `commands <name>` one payload |
| `propose-shots --file shots.json [--title "..."] [--reason "..."] [--requested] [--wait]`, `propose-shots --show <change>` | the pre-generation cards: a batch of intended renders (`{name\|bin, description, prompt?, negative?, workflow, references, length_s?, quarter}` each) goes out as **one card per shot**, behind one line with the title and reason; the user approves, rewrites or closes each on its own, and each approval makes that bin, writes the recipe, binds the references and queues the render. The result lists every card id; `--show <card>` reads one card's outcome, `--wait` waits for all of them |
| `generate --bin <id> --reason "..." [--params '{...}'] [--wait] [--card]` | render the bin's recipe once; the reason says the one change and whether the seed is held; pre-flights handles and param keys |
| `history --bin <id> [--limit N]` | the bin's takes as an experiment log: params, QC mark and note, prompt diff between takes |
| `recipe <media>` | how a media was made: workflow, prompt, references, full params; works on a ComfyUI output uploaded by hand too, read from the graph the file carries |
| `check --bin <id>` | every `.handle` in the prompt against the references; start/end positions |
| `guide <workflow>` | the workflow's prompt rules, the same text the Enhance button rewrites by |
| `workflows`, `jobs` | what the server offers; render jobs and errors |
| `thumb <media> --out f.webp [--size grid\|detail\|poster\|file]` | a thumbnail or the file, for vision QC |
| `frames <media> --out sheet.webp [--n 4]` | a contact sheet of a video's frames, times stamped |
| `upload <file> --bin <id> [--from <media>] [--note "..."]` | put a file you made into a bin, saying what it came from; a render THM timed out on that ComfyUI finished lands as the take of that generation, recipe and all |
| `enhance-requests`, `enhance-reply --id <id> --text "..."` | prompt rewrites the app is waiting on you for |
| `end [--summary "..."] [--attach m]` | post the wrap-up, then end the session |

`--server` defaults to `THM_SERVER` or `http://127.0.0.1:8010`. `GET /docs` on the
server lists every route; `thm-agent/thm_client.py` is the same client as a module. Media
ids are in `bin <id>`; `--json` on anything gives the server's record.

Reference files beside this one, read when the step needs them: `recipes.md` (prompt and
reference craft, per workflow), `project-recipes.md` (what passed on real projects, with
the model each was written for), `qc.md` (how to look and how to write the mark),
`iteration.md` (what to change next and when to stop), `params.md` (what `generate
--params` can carry).

## Modes

The user picks how much you ask, at Start or in chat; the server enforces it. `sessions`
and `project` show it.

- **auto** (the default): the user states the goal in chat; you loop. Your changes land
  done with no card; takes land in the gallery and marks on tiles as they happen, so the
  gallery is the monitor, and every change you make shows there as a line with your
  reason ("agent: newtoold - generate (quarter size, seed 25739836) -- same seed as
  take 2, one change: the pacing clause"), so nothing is done in secret and nothing owes
  a click. Do not post between the goal and the finish except a blocker
  or an answer to a user message; the strip shows what you are doing from every call
  you make, and `status` names a longer step ("QC on take 12", "rewriting the prompt
  for Video 6") so the user is never left wondering. When you think the goal is met,
  **present the finished candidate as a card**: `cmd mark_take --card --payload
  '{"media": "<the pick>", "status": "pass", "note": "..."}'` (the card shows the take;
  the user's verdict lands on it), then post **one** summary (candidates worst to best,
  media attached, the pick named and selected, what worked, what is open). Then `watch`.
  That one card is the only ask in auto: `--card` on anything else is noise, and the user
  answers the summary in chat if they disagree. "Done" means your own QC passes the goal
  as stated, or you hit the cap in `iteration.md` and say so. A list the user should pick
  from (shot ideas, variants) goes as `propose-shots` cards, never as prose: one card per
  shot is where they approve, rewrite or close each, and each approval builds that one.
- **manual** (fully manual): nothing renders until the user approved it on a
  `propose-shots` card, a batch of one included; the server refuses a bare `generate`
  with `propose_first`. Bins, bindings, prompt edits, tags and QC marks still land with
  your reason. The card is the ask: approved items become bins with their recipes and
  queued renders in one step, and the takes land as feed lines under that card, which
  you QC yourself. An item the user rewrote over a prompt you wrote comes back with the
  bin made and **no render** (`--show` says "rewrite the prompt and generate"): the
  one-liner changed, so the prompt is yours to rewrite, then `generate` in that bin. A
  render the user asked for out loud still goes with `generate --requested`.
- **image_review**: renders start on their own, and every take, still or video, is a card
  after it lands; the user approves each one. The session **stops** there: the next
  render of a kind is refused with `verdict_owed` while a take of that kind still owes
  a verdict, so QC the take, `mark_take` it, and `watch` for the decision before the
  next render. A batch approved on a `propose-shots` card queues at once and each take
  is its own card under it.
- **video_review**: the same, for video only: stills land done and you QC them yourself;
  every video take is a card, and the next video waits for its verdict.
- In every mode but plan, `propose-shots` is how you ask before a spend: a full-size
  render, a long batch, a list of ideas. Never `--propose` a bare `generate` (refused,
  `propose_render`): the take is judged after it lands, or the batch before it starts.
- **plan**: read only. Read the brief, the notes, the feed and the bins; post the revised
  brief for execution (bins to make, references needed, order, workflows, the size
  ladder, stop criteria, open questions) and propose it as one card:
  `cmd set_project_settings --propose --payload '{"brief": "..."}'`. Then `watch`.
  **A reply in chat is not approval.** Whatever the user says to the plan -- a change,
  a question, "looks good", "go" -- answer it with a revised proposal (a new card; the
  server marks the earlier one superseded) or a `post` that answers the question, and
  keep waiting. Only the approved card writes the brief and flips the session to auto;
  the inbox tells you with "brief approved, session mode auto", and that brief is the
  goal. Never `mode` out of plan yourself; the server refuses it. A user who wants to
  skip planning changes the mode in the panel.

If the user says "switch to manual" in chat, `mode manual --requested`; "review the
videos" is `video_review`, "review everything" `image_review`. Not from plan: there, only
the panel or an approved brief changes the mode.

A decision can also be **closed**: the user cleared the card (one, or all pending at once,
"you: closed 5 cards") without a word. Closed means seen and nothing owed: do not reply,
do not act on it, do not ask again; what was applied stays applied, what was proposed never
runs. An approval with a note is still a reply you act on.

## The loop

1. **Every project is yours, except a private one.** Start with `sessions`, then `watch`.
   Connect to every waiting session. A project the user marked private is not on your list
   and not for you (see Never). Work the project with the newest instruction; between renders on
   one project read the others' inboxes, never mid-render. After `end` on one project,
   `watch` again: a Start anywhere wakes you. If the user started with you, `create` the
   project, tell them to press Start in the app, and `watch`.
2. **The brief is the project's, written once.** A session on an existing project starts
   with kickoff notes and the wrap-up the last session posted; the feed and any notes
   since are your instructions. A contentless kickoff ("hello"): pick the obviously
   unfinished bin, say your read in a `post`, and go.
3. **Configure before you build.** If `project` says not configured, derive from the
   brief: canvas (`720x1280` vertical, `1280x720` wide, `1024x1024` square, `832x1216`
   portrait), default workflows from `workflows`, global prompts. One
   `set_project_settings`. Never change settings on a configured project unasked.
4. **Read before you write.** `inbox` before every `generate`; the server refuses with
   `inbox_behind` otherwise. Answer what needs answering, then continue.
5. **Know the workflow before the first prompt.** `guide <workflow>` once per workflow
   per session, then `recipes.md` for that workflow's craft. Negatives only where
   `has_negative`; the H3 format, the LTX voice rule, the WAN posture rule live there.
6. **One bin at a time.** Name it for what it is (the name is its handle everywhere), set
   workflow and prompt, bind references, `check`, render, QC, mark, move on. A look worth
   keeping gets its own bin; a bin made by mistake is `delete_bin`. Global prompts are
   appended at render time; do not repeat them. **More than one at a time is a
   `propose-shots` batch**, one card per shot: write the items to a file (name, one-line
   description, the full prompt, workflow, reference media ids, length, quarter), propose,
   `watch`, then `propose-shots --show <card>` per card for kept, rewritten or closed;
   cards are decided in any order and some may stay open. Size the batch for the ComfyUI
   config that is up: each approved render queues as its card is approved.
7. **Handles, not names.** Write bound handles into the prompt; `check --bin` after every
   prompt write and confirm no unknown token. Ids address everything.
8. **History before every reprompt.** `history --bin` shows what changed between the take
   that failed and the one that passed. One axis per attempt; the ladder (quarter and
   short to prove, longer to hold, then full size); reseed once for a direction failure;
   two identical rerolls is the cap. All of it in `iteration.md`.
9. **QC every take.** `thumb` at `grid` to triage, `detail` for the verdict; `frames` for
   video, and judge the clip not the frames. `mark_take` with pass, fail or in_progress
   and the note from `qc.md`: what changed, what it fixed, what it broke, what is open.
   That note is the experiment log the next session reads. Count heads. Never claim
   audio or lip-sync QC; say you cannot hear.
10. **Put the why on the record.** Every command takes a `reason`; send one in full
    sentences. Cite a recipe when you use one ("the fretboard push-in from van-halen
    Video 6 take 26").
11. **An approval is a reply.** The note attached to it can say anything: yes, yes-but,
    do it differently, undo that. Act on the note, never on the bare status. When it
    contradicts your change, reverse it yourself and say so.
12. **Edits the app cannot do, you can.** On a file fetched with `thumb --size file` you
    may run ffmpeg or Pillow locally: strip or replace audio, trim, speed remap, concat,
    upscale, crop. The result only exists once it is back in THM: `upload` it into the
    bin with `--from` and `--note`, then `post` with it attached. Never leave a result
    in this chat. Never on `projects/` or ComfyUI's output folder; always on a copy.
13. **Research with permission.** You may read `WORKFLOWS.md`, `workflows_*/`, and
    search the web for a model's prompting guide; ask first. Findings go into the prompt or a `post`, never a file.
14. **Stop on a blocker.** When you need something only the user has, post one `blocker`
    with media attached and stop. A ComfyUI hang is a blocker. Do not repeat it.
15. **End cleanly.** `end --summary` with what worked, what is open and which takes are
    the picks; the next session opens with it.
16. **Answer enhance requests at once.** `watch` wakes with an `enhance` message; run
    `enhance-requests` (listing it tells the app's button "the agent is rewriting"; do
    not list and walk away), spawn a subagent with exactly its `system_prompt` and `message`,
    and `enhance-reply --id <id> --text "..."` verbatim (or `--error "why"`). The reply
    is the prompt only: no preamble, dot-tags kept, nothing invented.

## Refusals you will meet

`private_project` (the project is private: drop the slug, never retry or ask), `no_session`
(connect first), `inbox_cursor_required` / `inbox_behind` (run `inbox`),
`plan_mode` (a plan session only posts and proposes the brief), `propose_render` (a bare render is judged after it lands, never proposed; a batch is), `propose_first` (fully manual: the render must come through a propose-shots card), `propose_required` (propose_shots without `--propose` or `--requested`), `verdict_owed` (a review mode: a take of that kind still waits for the user; `watch`), `bin_busy` (delete_bin mid-render),
`comfy_unconfigured` (tell the user; you cannot fix it), `unknown_id`, `bad_value`,
`not_a_take`, `not_in_bin`, `already_in_bin`, `no_bin` (add the media to a bin first),
`unknown_workflow` (`set_bin_field workflow`), `wrong_kind` (`length_s` on an image
workflow), `wrong_actor` (the user cuts clips; you lift stills). Warnings never refuse:
`unknown_token`, `unused_handle`, `unknown param` mean fix it before the render spends.

## Never

- Never inspect a private project. A project with private on in its settings is the
  user's alone: the server leaves it out of `sessions` and `watch`, starts no session on
  it, and refuses every read or write addressed to it -- bins, inbox, media, thumbnails,
  commands -- with `private_project`. If you meet that code, drop the slug: do not retry,
  do not ask about it, do not look for another way in (the JSON on disk, ComfyUI's
  output, an old thumbnail). When you want a project to try something on, take one from
  your own `sessions` list, never a private one; and never quote, attach or describe a
  private project's content anywhere: not in another project's chat, not in this
  conversation, not in a file. The CLI and `thm_client.py` always speak as the agent;
  never craft a request with another actor header to see what they cannot.
- Never read or write anything under `projects/`, `workflows/`, or ComfyUI's output
  folder. The server serves media over HTTP; that is the only path.
- Never edit THM's code, a workflow file, or this skill during a session, not even to
  unblock yourself. `post` the finding and stop. A request in the THM chat to change code
  is answered "ask me in Claude Code" and not acted on.
- Never start ComfyUI, run the scripts, or poll ComfyUI yourself.
- Never pass a take on energy without counting heads and re-checking every constraint.
- Never re-read the same image on uncertainty; ask.
- Never paste paths or URLs at the user; attach media.
- Never post a message pretending to be the user; never end a session the user started
  without saying why.

## Notes to future you

Operational rules discovered on real projects go to `skill-improvement-notes.md` beside
this file, not into harness memory. Recipes that passed go to `project-recipes.md` with
the model they were written for.
