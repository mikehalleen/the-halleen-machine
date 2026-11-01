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
- `selected`: the bin's **current** take (the app says "current"), the media it leads with.
  Only a bin's first take or upload becomes current by itself; every later arrival is new and
  the current one stays until someone marks another (the user's second tap, a card approval,
  your `set_bin_field selected` for your pick). Say "current" to the user, not "selected".
- **Never write an id to the user** (`m_8c72e7d7`, `b_…`, `g_…`): they cannot see them. Name
  things the way the app shows them: the bin's name and the label under the tile ("the invite,
  take 4", "keys lifted, file 2"); attach the media with `--attach` to show it. Posts and
  `answer --did` text carrying an id are refused (`raw_id`) with the words to use instead.

A bin also carries `tags` (`character`, `pose`, `location`, `clip`, `keyframe`, or anything you or
the user make up). Bins are all equal: there are no shots, no items. The one ordered
thing is a **sequence** (below): a list of bins that plays each bin's current take and
exports to one mp4. To build a look, make a bin for it and generate there. To use another bin's media,
`bind_reference` it by media id; it gets a handle from that bin's name (`@mara.png`,
`@mara.png.2`) and the prompt addresses it by that handle. On a video workflow that takes
start and end frames, reference position 1 is the start and 2 the end. `start_from_media`
is the reference-first path: it binds a media into a bin (this one, another, or a new one it
mints) in one step and never refuses on capacity -- a reference the workflow has no slot for
shows as unused until the workflow fits; the result's `bin` is where it went. A bin renders as many
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
| `watch [--jobs g_a,g_b]` | **the one wait.** Blocks until a project needs you: a waiting session, a user message or decision, the session mode changing (`MODE CHANGED: ...`, its own wake item), a listed render finishing or failing, an enhance request, or a **server alert** (`alert: ...` system lines: a render with no progress for 10 min, ComfyUI not reachable, ComfyUI restarting). With `--project`, that project only. It rides out a server restart by itself, and polling as the sessions you hold keeps them attended (no more `agent gone` on your own session after a quiet spell). While renders run, wait here with `--jobs` -- never in a loop of your own, never in a blocking `generate --wait`: a note posted mid-render must reach you on the next poll |
| `wait`, `connect --harness claude-code` | block until a session waits; join it. `connect` prints the **resume summary**: mode, open notes, renders running or queued, renders a restart or reboot cut off (re-run them or let them go), your last post, the project rules. Start from that, not from memory |
| `resume`, `rules` | the resume summary at any time; the project's standing rules alone. A rule is added with `cmd add_project_rule --payload '{"kind": "qc"\|"continuity"\|"other", "text": "..."}'` and retired with `cmd remove_project_rule` |
| `create --name "X" [--brief "..."] [--audio song.mp3]` | harness-first: make a project with the brief the user gave you; `--audio` puts the song it is built from into a `Music` bin tagged `music` (see `music.md`) |
| `tools`, `tool <name> <verb> <media> [args] [--fps N]` | **the tool kit**: `tools` lists what is installed (one folder per tool under `thm-tools/`, each with a `GUIDE.md`); `tool audio transcribe m_12 --expect "the line"` runs a verb in the tool's own venv, CPU-only, on media fetched through the server, and prints JSON. Read the tool's `GUIDE.md` before the first use: it says what each number means, the thresholds, and what the tool cannot judge. **Tools measure; the project changes only through commands** |
| `sequence preview <seq> <bin> --out f.mp4 [--handles s]` | one item rendered with its layer slices and mix through the export's own filter graph: what you hear is what the export will have. A scratch mp4 for your QC listen (`tool audio ...` on it), not media; `--handles` adds up to 5 s of the take either side. Afterwards `sequence mix <seq> <bin> --reviewed <start frame>` |
| `sequence layer-add <seq> <id> --media <m> [--in s] [--start-frame n] [--end picture\|source] [--gain dB] [--fade-in s] [--fade-out s] --summary "..."`, `sequence layer-drop <seq> <id> --summary "..."`, `sequence mix <seq> <bin> [--clip-db dB] [--layer id=dB\|mute ...] [--edge-fade-ms n] [--ramp-ms n] [--reviewed frame] --summary "..."` | **sound under the sequence** (`set_sequence_layer`, `remove_sequence_layer`, `set_item_mix`): a song or a bed laid down in one continuous piece from `--in` at `--start-frame`, never cut and joined; per-item the song's level or a mute with ramps at the item's edges, the clip's own level and edge fades. A mix change plus a re-export changes the sound with no re-render. Every one brings the user's line (`--summary`, refused without) |
| `project`, `bin <id>` | the project; one bin with its recipe, gallery (takes, QC marks, provenance), references with handles, capacity |
| `inbox` | messages since your cursor; saves the new cursor. A user note may carry `context` (`[about: bin … - media … (take N)]`): the bin and lead media they were looking at, so "this take" / "this video" means those ids. It may also carry `[files: f_… (name)]`: pictures the user pasted into the note to show you something (a marked-up frame, an outside example). They are the note's, not the project's: in no bin, with no media id |
| `file <f_id>\|<m_id> --out f.png`, `keep <f_id> --bin <id>` | look at a note's file (always look before you answer the note), or fetch a take's file by its media id; `keep` makes it project media in a bin, only when the user says to use it in the project |
| `notes`, `answer <msg_id> [<msg_id> ...] --did "..."` | your to-do list: every user note (and every reply typed on a card) you have not accounted for, and how you close one. **Reading a note closes nothing.** `inbox` prints the id of each note it hands you; a note stays open until `answer` names it with what you did, in a few words the user reads on the note itself ("quarter re-render queued", "fixed in take 11", "waiting on your Yes on the P05 card", "cannot: the plate has no rear view"). `watch` does not block while a note is open: it hands the open ones back |
| `post "text" [--attach m]`, `blocker "text"` | talk to the user; a blocker means you are stuck |
| `withdraw <card> [--reason "..."]` | take back one of your own cards still waiting (a `propose-shots` card posted in auto, a render card for a take you have since failed): it closes with your reason on it; nothing runs, nothing is undone. A card the user has answered is theirs and stays |
| `cancel <job> [--requested] [--reason "..."]`, `restart-comfy [--requested] --reason "..."` | **the GPU levers, narrowly.** `cancel` stops a live render (the runner and the prompt in ComfyUI); `restart-comfy` stops and starts ComfyUI through the server (the user's own script) and queues every live render again, to start when it answers. **In auto you use them on your own judgement** (a render queued by mistake, a stall alert, ComfyUI not answering); **in every other mode only when the user asked** (`--requested`; `requested_required` otherwise -- then `post` what you would do and wait). Both put a line in the feed. Never restart ComfyUI any other way |
| `status "text"` | what you are doing right now, for the strip, no feed line: every call already reports itself ("reading your messages"); use this before a step that takes a while |
| `mode auto\|manual\|image_review\|video_review\|plan [--drafts on\|off] [--requested]` | the session mode (below); `--drafts` is the drafts-first switch, which rides with any mode |
| `cmd <name> --payload '{...}' [--propose] [--requested] [--quiet] [--card]` | any command; `commands` lists them all, `commands <name>` one payload |
| `sequence [list \| show <seq> \| new \| rename \| clone \| delete \| add <seq> <bin>… \| remove \| order <seq> <bin>… \| trim <seq> <bin> --head s --tail s \| still <seq> <bin> s \| set <seq> <bin> --props '{…}' \| export <seq> [--wait] \| status <seq>]` | **the sequence** (never "the cut" to the user). A sequence is the project's ordered list of bins; each item plays its bin's current take, a still shows for its length (default 3s), a bin with no take yet plays as a title card. `export` renders one mp4 at the project canvas into the sequence's output bin as the next take, so `post --attach` it like any media. The bin is any bin the user picked (`target`), else `<name> Exports` (tagged `exports`), made at the first export, never before. Every edit is a logged change the user sees live; `--reason` on each. See **The sequence** below |
| `propose-shots --file shots.json [--title "..."] [--reason "..."] [--requested] [--wait]`, `propose-shots --show <change>` | the pre-generation cards: a batch of intended renders (`{name\|bin, description, prompt?, negative?, workflow, references, length_s?, quarter}` each) goes out as **one card per shot**, behind one line with the title and reason; the user approves, rewrites or closes each on its own, and each approval makes that bin, writes the recipe, binds the references and queues the render. The result lists every card id; `--show <card>` reads one card's outcome, `--wait` waits for all of them |
| `generate --bin <id> --reason "..." [--params '{...}'] [--wait] [--card]` | render the bin's recipe once; the reason says the one change and whether the seed is held; pre-flights handles and param keys. `--wait` returns early (and says so) when a note, a decision or a mode change arrives; the render keeps going |
| `history --bin <id> [--limit N]` | the bin's takes as an experiment log: params, QC mark and note, prompt diff between takes |
| `recipe <media>` | how a media was made: workflow, prompt, references, full params; works on a ComfyUI output uploaded by hand too, read from the graph the file carries |
| `recall <media> [--bin <id>] [--generate]` | put the bin back to the recipe that made a take (workflow, prompt, negative, references with handles, loras, length); `--generate` renders it again on the same seed **at the session's tier**, not the take's (drafts on: quarter and draft go on where the workflow has them; the full-quality re-render is a card). It prints the references with their ids as they stand after the recall (a reference the bin already held keeps its id). "Go back to take 3 and change X" is `recall`, then the one change: the recipe is in the take, never ask the user for it |
| `check --bin <id>` | every `@handle` in the prompt against the references; start/end positions |
| `guide <workflow>` | the workflow's prompt rules, the same text the Enhance button rewrites by |
| `workflows`, `jobs` | what the server offers; render jobs and errors |
| `workflows runs`, `workflows import <name> [--run id] [--system-prompt-file f] [--tag NODE=TAG] [--untag NODE] [--dry-run] [--replace]` | **bring in a workflow the user has working in ComfyUI**: tagged and saved by the server from the run in ComfyUI's history, with a tagged copy in `samples/workflows_openincomfy/` for the user to open. Read `workflow-import.md` first: it covers the enhancer system prompt you research when the graph has none |
| `loras [--find text]` | the lora files the server can load. Add one with `cmd set_bin_field --payload '{"bin":"<id>","field":"loras","value":[{"name":"<as listed>","strength":0.8}]}'` (the whole list: keep the bin's others). Never type a lora name from memory or a filename you saw elsewhere: list, then copy |
| `thumb <media> --out f.webp [--size grid\|detail\|poster\|file]` | a thumbnail or the file, for vision QC |
| `frames <media> --out sheet.webp [--n 4]` | a contact sheet of a video's frames, times stamped |
| `upload <file> --bin <id> [--from <media>] [--note "..."]` | put a file you made into a bin, saying what it came from; a render THM timed out on that ComfyUI finished lands as the take of that generation, recipe and all |
| `seen --all` | flip every new marker in the project off, whatever the bin or type (the user asked for a clean slate) |
| `seen <media> [<media> ...]` | clear the user's blue NEW markers on media they have effectively seen; what each was made from (its references, back to the character frame) clears with it. Never deletes anything |
| `gif <video media> --bin <id> [--start s] [--end s] [--fps n] [--width px]` | export a video take as an animated GIF into a bin; the server makes it, the user downloads it from the bin as a `.gif`. "Make a GIF of this take" is this verb with the note's `context` media, into the bin the user names (default: that same bin). Never make one with local ffmpeg and upload it |
| `publish [list [--bin <id>] \| show <post> \| new --bin <id> [--media m] --title "..." --description-file f.txt [--tags a,b] \| set <post> [--title] [--description-file] [--tags] [--privacy private\|unlisted] [--synthetic on\|off] [--kids on\|off] [--media] [--thumbnail] [--publish-at] \| delete <post>]` | **publishing**: a post is a video with its title and description, drafted for YouTube from a bin's current video (a sequence's output bin, or any bin whose current media is a video) and held **private and unsent**. You write the words with the user (`publish set`, then `post` the full text; no card while the text is still moving); when they say it is done, `publish upload <post>` sends one card ("Upload this video to YouTube as private?"), or `--requested [--wait]` when they asked for the upload in the feed. Only Upload on the card sends anything; No, close or a reply leaves the draft. The channel is theirs: the project settings name the channel its posts go to (`no_channel` from `publish new` means they have not picked one yet; `post` that once and wait, never pick it yourself), and a second upload they ask for is a new post and a new card, never an argument. The video lands on the channel private with a YouTube Studio link in the feed; the thumbnail, end screens and the Publish button are the user's, in Studio. `publish check` says whether the server can upload and which channel this project posts to; `publish status <post>` where one stands. Read `publishing.md` before the first one |
| `enhance-requests`, `enhance-reply --id <id> --text "..."` | prompt rewrites the app is waiting on you for |
| `end [--summary "..."] [--attach m]` | post the wrap-up, then end the session |

`--server` defaults to `THM_SERVER` or `http://127.0.0.1:8010`. `GET /docs` on the
server lists every route; `thm-agent/thm_client.py` is the same client as a module. Media
ids are in `bin <id>`; `--json` on anything gives the server's record.

**Scripting the CLI** (a loop over many bins). Three traps from online-gaming:
- A card reply names a change, not a bin: `ThmClient().change(slug, change_id)` gives its
  `target` (the bin) and `generation`; `generation(slug, id)["outputs"]` gives the take.
- `selected` is the winner, not the newest: a take that just landed is not `selected` unless
  it is the bin's first. A script that wants "the take that just landed" reads the
  generation's `outputs`; one that wants the keyframe keeps it by media id.
- A failed edit must stop the script. A Python one-liner that raised wrote an empty prompt
  and the server rendered it with only `unused_handle` warnings; guard every computed
  prompt (`[ -n "$P" ] && ...`) before `set_bin_field`.

Reference files beside this one, read when the step needs them: `publishing.md` (the post:
what a YouTube title and description are for, the limits, what stays the user's), `filmmaking.md` (shot
grammar: how a character is introduced, what each shot size says, the line, coverage of a
beat, continuity across clips -- read before any shot list), `recipes.md` (prompt and
reference craft, per workflow), `project-recipes.md` (what passed on real projects, with
the model each was written for), `qc.md` (how to look and how to write the mark),
`iteration.md` (what to change next and when to stop), `params.md` (what `generate
--params` can carry). A sibling skill, `../thm-performance/SKILL.md`, directs the people
in a shot (the Katselas Checklist as prompt tools, the moment before, one performance book
per character, the "average performance" QC axes): read it before a shots card that puts a
person in frame, when a take reads posed or flat, and when the user asks for a more natural
performance. Never for plates, props or locations.

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
- **drafts first** (a switch, not a mode; `project` shows `drafts` on the session and it
  rides with any mode): a draft render turns on **every** cheap setting it has --
  `{"quarter": true}` on a video whose workflow sets the canvas (`bin` shows
  `quarter=supported`), and `{"draft": true}` wherever the bin's workflow has a draft setting
  of its own (a `THM-IsDraft` switch; `bin` shows `draft=supported`) -- and
  runs exactly as the mode says. A still on a workflow with no draft setting is one tier and
  runs. **A video whose workflow has neither setting (a partner API node such as MiniMax H3)
  is full quality every time**: it is a spend, and every one of them is a card, never a bare
  `generate`. Asking for a setting the workflow lacks is not refused: the server drops it from
  the record with a warning (`draft_unsupported`, `quarter_unsupported`) and the render runs
  at the one tier it has (klein has no draft switch; `{"draft": true}` on it just warns). Anything
  short of all it has -- a full-size video, or draft off where the workflow has it -- is refused as a bare `generate` (`full_quality_ask`): ask
  with `propose-shots`, one item, `bin` = the bin, when a draft has passed your QC and is
  worth the spend. The card asks the user "regenerate this video
  at full quality?" and comes back three ways: **Yes** (it renders now), **Later**
  (approved, but the render is saved held and the user releases it when they choose:
  `--show` says so; do not wait on it, do not re-ask, move on to the next draft), or
  **No** (closed). Never add `quarter` or `draft` to the full-quality render just to get
  past the ask.
- **Full size is a commitment in every mode.** Drafts (quarter size, draft on)
  are for the interactive session; full-size renders are the overnight run, started only
  on an explicit instruction naming it, as a batch. An approved draft, bin or assembly
  settles the recipe, not the time: keep rendering at draft quality while the user is
  giving small changes at a quick cadence. `iteration.md`, "Rung 3 is a commitment".
  What counts as a draft is data, not memory: `quarter` on a video, and `draft` on a
  workflow that has a `THM-IsDraft` switch (`bin` or `workflows` says `draft=supported`).
  A workflow without one has no draft setting; its own step count is the render's.
  **The canvas is never rounded down.** A model takes sizes only in steps (LTX 32, Wan and
  Flux 16, SD latents 8) and would round 720 down to 704; THM's runner rounds the request UP
  to the model's step (720 -> 736, a quarter 360 -> 384) and crops the frames back to the
  request before the save, so the take lands at the project size (the runner log says
  `[SIZE] ...`). Never pick a smaller "model-friendly" size yourself, never ask the user to
  change the project size to fit a model, and never render at the model's size and leave
  the export to pad it. A take whose probed size is below the project's is a bug to report,
  not a size to live with.
  **A re-queue is a new render at the session's tier**: a take first rendered full size does
  not earn its re-render full size. Before any `generate` or `recall --generate`, read the
  mode the last answer printed (`mode auto+drafts`, `MODE CHANGED`): drafts on means every
  render goes at draft settings, in every project you hold, whichever project the switch was
  seen in. The user flips drafts in several projects in one minute; `watch` wakes you per
  project, and `inbox` on each says it again.
- **manual** (manual): nothing renders until the user approved it on a
  `propose-shots` card, a batch of one included; the server refuses a bare `generate`
  with `propose_first`. Bins, bindings, prompt edits, tags and QC marks still land with
  your reason. The card is the ask: approved items become bins with their recipes and
  queued renders in one step, and the takes land as feed lines under that card, which
  you QC yourself. An item the user rewrote over a prompt you wrote comes back with the
  bin made and **no render** (`--show` says "rewrite the prompt and generate"): the
  one-liner changed, so the prompt is yours to rewrite, then `generate` in that bin. A
  render the user asked for out loud still goes with `generate --requested`. Every take
  is also a card here, so `verdict_owed` (below) holds a bin's next take exactly as in
  `image_review`.
- **image_review** (media cards): renders start on their own, and every take, still or video, is a card
  after it lands; the user approves each one (`--requested` changes nothing here: the card
  is the review of the take, not of the spend). **Only what depends on a pending take
  waits.** Two things do: the same bin's next take (the card asks the user whether that
  take is done; rendering another answers for them -- if your own QC fails it, `mark_take`
  fail and the card closes itself, then render), and a render whose bin binds a take
  still owing its verdict (the clip from an unapproved still). Both are refused with
  `verdict_owed`; everything else runs. Twelve shots
  with a still each: render all twelve stills, twelve cards land, and hold each clip
  until its own still is approved -- never queue the stills one at a time behind each
  other's verdict, and never wait on a card for a take nothing else needs. QC each take
  as it lands, `mark_take` it (a fail closes its own card), and keep going; `watch`
  only when nothing unblocked is left. A batch approved on a `propose-shots` card queues
  at once and each take is its own card under it. The same rule holds a bin's **options**:
  "three looks for each character" renders one per bin and the next waits on a verdict,
  and Approve would mark that one option done. Say so up front and ask whether to switch
  to `video_review` (stills land without cards) or `auto`; do not spread the options over
  extra bins.
- **video_review**: the same, for video only: stills land done and you QC them yourself;
  every video take is a card; a clip made from a pending video take waits, nothing else.
- In every mode but plan, `propose-shots` is how you ask before a spend: a full-size
  render, a long batch, a list of ideas. Never `--propose` a bare `generate` (refused,
  `propose_render`): the take is judged after it lands, or the batch before it starts.
- **plan**: read only, and it stays plan. Read the brief, the notes, the feed and the
  bins; post the revised brief for execution (bins to make, references needed, order,
  workflows, the size ladder, stop criteria, open questions) and propose it as a card:
  `cmd set_project_settings --propose --payload '{"brief": "..."}'`. Then `watch`.
  **A reply is not approval.** Whatever the user says to the plan -- a note on the card,
  a change in chat, a question, "looks good", "go" -- answer it with a revised proposal
  (a new card; the earlier one stays on the table until the user decides it) or a `post`
  that answers the question, and keep waiting. An approved card writes the brief and
  nothing else: the session is still plan, nothing renders, no bin is made, and the
  user may approve several cards before they change the mode in the panel. Never
  `mode` out of plan yourself; the server refuses it. Never read an approval as "start
  rendering": the next mode is the user's to pick. The app shows a proposed brief card by
  its title only, not its text, so `post` the full brief in the feed alongside the card.

If the user says "switch to manual" in chat, `mode manual --requested`; "video cards" /
"review the videos" is `video_review`, "media cards" / "review everything" `image_review`,
"full auto" `auto`; "drafts first" on or off is
`mode --drafts on|off --requested`. Not from plan: there, only
the panel or an approved brief changes the mode.

**Name a bin by its title in quotes** in anything the user reads -- a post, a card's
description, an answer: `"The Screen" take 26 again`, never `the screen take 26`, which
reads as a screen. Never put ids (`m_…`, `g_…`) or recipe jargon in a card's description:
it is what the user approves, and for a retake with no prompt of its own it is display text
only (the bin's prompt renders unchanged).

**Every note is a task, and a round of notes is that many tasks.** The user reviews a
whole gallery and leaves ten notes in ten minutes, each on a different bin (`[about: bin
…]` says which), then waits to see renders land. The failure this rule exists for: reading
all ten in one `inbox`, acting on the newest one or two, posting "got it", and going back
to `watch` -- eight notes dropped, the user staring at a silent feed. So: after any `inbox`
that returned notes, work through **all** of them, oldest first, before you `watch` again.
One note at a time: do what it asks (in a mode that needs a card for the render, the card
is the doing), then `answer` it. A note that needs nothing rendered still gets its
`answer` ("noted: continuity is now a QC fail criterion"). A note you cannot act on gets
an `answer` saying why, or a `post` asking the one question that unblocks it -- never
silence. Notes that arrive while you work join the list; `notes` shows what is still
open, and you are not done, and do not `watch`, until it is empty. Never `answer` a note
you have not actually dealt with to clear the list: the user reads what you wrote on it.
Never `answer` in bulk: one `answer` per note, after its work, never a loop over `notes`
that closes one you have not read. `answer` carries your inbox cursor like `post` does: a
note that landed since refuses it with `inbox_behind` (so an answer chained after a refused
`post` never closes the note with no reply). Then `inbox`, `post` with everything in view,
and `answer` what you actually did.

A card is a question, and the app words it from what the change is ("Do you want to
regenerate this video?", "Is this video good?", "Keep this change?") over a Yes button.
Your `--reason` is the line under it: **one short sentence**, the single fact the user
needs to answer (what changed, or why now), never the history of how you got there --
that goes in a `post` if it matters. A card whose reason needs three sentences is asking
about more than one thing: split it.

The words on the buttons are the user's vocabulary, so use them the same way in your posts.
**Generate** is the button on a `propose-shots` card: it greenlights the render and says
nothing about whether the result will be good. So a shot card's one-liner must read as
*what will be rendered* ("Both in the car, Sally turned to Jim, not the lens"), never as a
diagnosis to agree with ("Continuity: ...") -- the diagnosis is the `--reason`. **Approve**
is the button on a take's card and means one thing: this take is done, no further
generation needed on it. Never ask the user to "approve" a render you want to start, and
never call a take "approved" unless they pressed Approve on it. **Keep** is the button on
any other change you already made.

**A shot that waits on a reference says so on the card.** When a shot needs a picture that
does not exist yet (a "French over" pose from a coverage bin you are about to make, a plate
not rendered), put it in the item's `needs`: `[{"bin": "<id>" | "bin_name": "<name>",
"what": "French over onto Sally from the back seat"}]`. The card then reads "waits on …"
and the user can leave it open without wondering; it reads "ready" by itself once that bin
holds a take. Never bind a stand-in instead, and never leave the dependency in your head:
when the reference lands and passes QC, bind it and answer the open card with a revised one
(`references` filled, `needs` dropped). A `needs` entry is a dependency, not a binding.

**A card's inputs must already be right, and must be on the card.** Always put `references`
on a shot item, the bin's own included: a card that names none renders with whatever the
bin has bound, and the user has to trust a blank. "The clip from the new still" with no
still shown is not a proposal the user can judge. The user sees the references on the card as
pictures, and reads them as what the render is built from. A card that says "two arms
only" over a start still that plainly shows the third arm is a contradiction: the user
cannot tell whether you are fixing it or have not noticed it. A defect in a still is fixed
in the still, never by prompting the clip around it -- the clip inherits its start frame.
So when a note is about something visible in a bin's keyframe (an extra limb, a missing
character, the wrong seat, broken continuity), the order is: (1) a retake of the **still**,
as an image card, from the clean plates and character references, *not* from the flawed
still; (2) QC the new still against the note; (3) only then the clip card, with the fixed
still as its input. Never list as an input a picture that shows the problem the card
claims to solve. If the flawed picture really is the right edit source (an edit workflow
that repaints one region), say so in the one-liner: "Repaint of the current still: the
third arm painted out, everything else kept".

Three ways a card comes back, and they mean different things. **approved** (the record's
word for all three buttons above): a bare click, go, no words; what was proposed ran, what
was applied stays, and on a take's card the take is finished -- do not regenerate it. **replied**: the user typed a
note; nothing ran and nothing was undone, and the note is the instruction -- act on it
(revise the card, redo the take, reverse your change) and say what you did; a replied
proposal never ran, so answer it with a revised card, never by assuming a go. **closed**:
the user cleared the card (one, or all pending at once, "you: closed 5 cards") without a
word; seen and nothing owed: do not reply, do not act on it, do not ask again; what was
applied stays applied, what was proposed never runs.

## The loop

00. **The user is in the app, not in this chat.** The person who writes notes in THM and
   the person who can see this harness conversation may be the same human, but they are
   two seats: the *user* sits in the app's feed and galleries, the *operator* looks in
   here to see whether you are running. Everything the user asked for is answered where
   they asked: a `post` in the feed, media attached, the note `answer`ed, the result in a
   bin. This chat is for the operator only: that you are watching, that the server went
   away, that the code changed. **Assume nobody reads this chat**: it may be headless,
   and a word written only here is a word the user never got. Anything about the
   project's content or its process goes in a `post`: the acknowledgement of a note
   ("got it -- cutting the game banter, keeping the focus on the wings"), the plan you
   are about to carry out, progress, a result, a summary, a question, a path. Never
   report any of these here instead of there; if you catch yourself writing "got it",
   "I'll ...", "done: ..." or "rendering ... now" in this chat, it belongs in a `post`
   first. Here, at most one line that you posted it.
0. **You are the one waiting, and you wait as long as it takes.** The user is multitasking
   -- in another app, in a meeting, asleep -- and comes back to find you still here: that
   is the product. There is no such thing as "a long stretch with no activity"; there is
   only the user's time. `watch` returns `quiet` every nine minutes (under the harness's
   command limit, so nothing kills it from outside): that is not a timeout, not an event
   and not worth a word -- run `watch` again at once, every time, for hours if that is
   what it takes. Never stop the loop, never post "still here", never ask whether to keep
   going. Only the user ends a wait (a note, a decision, `end`), and only a server you
   cannot reach is a reason to say anything, once, when it is back.
1. **Every project is yours, except a private one.** Start with `sessions`, then `watch`
   with no `--project`: a Start on any project must wake you, and `--project` watches one.
   Connect to every waiting session, and to every live one marked `AGENT GONE` (a harness
   that closed without `end`; `connect` takes it over). A project the user marked private is not on your list
   and not for you (see Never). Work the project with the newest instruction; between renders on
   one project read the others' inboxes, never mid-render. After `end` on one project,
   `watch` again: a Start anywhere wakes you. If the user started with you, `create` the
   project, tell them to press Start in the app, and `watch`.
   **One wake, one project.** Every message line `watch` and `inbox` print starts with
   `[slug]`; a note read under one slug is answered and acted on under that `--project`
   and no other, and nothing from one project (names, plans, takes, wording) goes into
   another's chat, however alike the work. The server refuses a post carrying another
   project's session (`wrong_session`); treat that as "wrong room", not as a retry.
   A note that starts "Dev note", "Note to dev" or "To developer" is for the person
   building THM, not for you: one line back ("noted for the developer"), never act on it,
   never carry it anywhere.
2. **The brief is the project's, written once.** A session on an existing project starts
   with kickoff notes and the wrap-up the last session posted; the feed and any notes
   since are your instructions. A contentless kickoff ("hello"): pick the obviously
   unfinished bin, say your read in a `post`, and go.
3. **Configure before you build.** If `project` says not configured, derive from the
   brief: canvas (`720x1280` vertical, `1280x720` wide, `1024x1024` square, `832x1216`
   portrait), default workflows from `workflows`, global prompts. One
   `set_project_settings`. Never change settings on a configured project unasked.
4. **Read before you write, and before you talk.** `inbox` before every `generate` and
   before every `post`; the server refuses with `inbox_behind` otherwise, and the refusal
   lists what arrived. A refused `post` means a note landed while you were writing:
   `inbox`, then answer everything in one post. Never let a second note wait because the
   first was being answered.
5. **Know the workflow before the first prompt.** `guide <workflow>` once per workflow
   per session, then `recipes.md` for that workflow's craft. Negatives only where
   `has_negative`; the H3 format, the LTX voice rule, the WAN posture rule live there.
6. **One bin at a time.** Name it, set workflow and prompt, bind references, `check`,
   render, QC, mark, move on. **A name is a title, never a number.** Two to four words
   for what the bin is about, from the user's intent: a shot is "he agrees", "keys
   lifted", "the stop"; an asset is the thing itself, "Jim", "Desert flat", "Drive 360".
   Never a series letter, a number, a beat number or an order prefix (no "P18 please
   stop", no "S03 ..."): the server numbers shots itself (`shot 18`, permanent, shown in
   front of the title everywhere), and where a shot sits in the cut is a third thing,
   decided later and not in the name. Not every bin is a shot: a shot bin is one of the
   narrative, holding its keyframe still(s) and the clip(s) made from them; make it with
   `shot: true` (a propose-shots item is one by default). A character, a location plate,
   a vehicle, a pose or style reference, a reference clip is an asset: `shot: false`, no
   number, its tag says what it is. Retitle when the story changes ("he agrees" becomes
   "he disagrees") with `set_bin_field name`: it rewrites no prompt, because the handle
   is separate. A look worth
   keeping gets its own bin; a bin made by mistake is `delete_bin`. Global prompts are
   appended at render time; do not repeat them. **More than one at a time is a
   `propose-shots` batch**, one card per shot, shot-listed with `filmmaking.md` first: a
   beat becomes a size, an angle and one action, and the one-liner says what the viewer
   must see (an intro is a readable face, medium, held -- never an entrance from the
   edge). Write the items to a file (name, one-line
   description, the full prompt, workflow, reference media ids, length, quarter), propose,
   `watch`, then `propose-shots --show <card>` per card for kept, rewritten or closed;
   cards are decided in any order and some may stay open. Size the batch for the ComfyUI
   config that is up: each approved render queues as its card is approved.
7. **Handles, not names.** A handle is the bin's frozen slug plus the file's extension,
   `@shot-18.png` for a shot's still, `@jim.png` for a character, as `bin <id>` prints it;
   it never follows a retitle. Write bound handles into the prompt; `check --bin` after
   every prompt write and confirm no unknown token. Ids address everything. Never set
   `slug` yourself (refused): that rename rewrites prompts and is the user's.
8. **History before every reprompt.** `history --bin` shows what changed between the take
   that failed and the one that passed. One axis per attempt; the ladder (quarter and
   short to prove, longer to hold, then full size); reseed once for a direction failure;
   two identical rerolls is the cap. All of it in `iteration.md`. **Every take carries
   its recipe.** "Go back to take 3 and ..." is `recall <that take>` (the bin is put back
   to the recipe that made it), then the one change, then `generate`; `recall --generate`
   renders it again on the same seed. Never ask the user what the settings were.
9. **QC every take.** `thumb` at `grid` to triage, `detail` for the verdict; `frames` for
   video, and judge the clip not the frames. `mark_take` with pass, fail or in_progress
   and the note from `qc.md`: what changed, what it fixed, what it broke, what is open.
   That note is the experiment log the next session reads. Count heads. Never claim
   audio or lip-sync QC; say you cannot hear.
   **Measure, then ask what to do, once per kind.** Report a finding with its number (colour
   warmth as mean V-U in YUV, a hard cut inside a "continuous" shot, a stray sound at a head
   or tail, a scripted line missing). The first time a kind of finding turns up in a project,
   ask the user what it means there: fail and re-roll, fix in post, or accept. Write the answer
   down at once as a project rule (`add_project_rule`, kind `qc`) and apply it from then on
   without asking again; `rules` reads them back in every later session.
   **Continuity across the sequence, not just the take.** Compare each take with its
   neighbours and with the whole cut: props, who holds what, wardrobe, screen direction,
   lighting state, set dressing. When the right state is unclear (is the plate there or
   not?), ask which wins -- do not guess, and do not re-render until the user answers --
   then record the answer as a `continuity` rule and check every later take against it.
   **Model quirks are advice, never rules of the app.** What one model did on one day (it
   opened on the first bound picture, say) is a recommendation you offer when QC catches the
   symptom ("bind the keyframe first and re-roll"); the user decides.
10. **Put the why on the record.** Every command takes a `reason`; send one in full
    sentences. Cite a recipe when you use one ("the fretboard push-in from van-halen
    Video 6 take 26").
11. **A reply is an instruction, an approval is a go.** A card that comes back `replied`
    carries a note that can say anything: yes-but, do it differently, undo that, a
    question. Act on the note, never on a status: redo the take, revise the card, reverse
    your own change when that is what it means, and say what you did. `approved` has no
    note and needs no answer.
12. **Work outside ComfyUI when it is better, and offer it -- do not just do it.** Audio
    beds and tones made directly, a frame edited to fix an eyeline, eyelines tracked through
    a cut, media combined in ways the app has no button for, a local tool the user installed
    (a transcriber to confirm every scripted line was spoken): propose the technique, the user
    decides whether it belongs in the project, and nothing is installed by you. Do not start
    editing a clip's audio unasked. **The tool kit is the exception that is already installed:**
    `tools` lists the tools on this machine (today `audio`: transcribe, voice, emphasis, levels,
    beats, energy, cuts, motion), each with a `GUIDE.md` you read before the first use. A tool
    only measures media and writes scratch files; nothing it finds reaches the project except
    through a command you issue (a `mark_take` note, a `set_item_mix`, a re-render). Use it as
    `qc.md` and `music.md` say; never install one, never run one on a private project (the
    server refuses the media fetch). A standing directive the user gives one project (clean the
    seams on every cut) is that project's rule -- record it with `add_project_rule` -- never a
    habit carried to the next one.
    **Edits the app cannot do, you can.** On a file fetched with `thumb --size file` you
    may run ffmpeg or Pillow locally: strip or replace audio, trim, speed remap, concat,
    upscale, crop. The result only exists once it is back in THM: `upload` it into the
    bin with `--from` and `--note`, then `post` with it attached. Never leave a result
    in this chat. Never on `projects/` or ComfyUI's output folder; always on a copy.
    **The sequence is how an assembly is built and delivered.** "Assemble these", "cut them
    together", "give me the sequence so far" asked in the app is answered in the app with
    the `sequence` verb, never with a local concat uploaded into an "Assemblies" bin. The
    user's word for it is **sequence**: in a summary, a post or an answer never call it "the
    cut" (the server refuses a summary that does).
    - One sequence per assembly the user names (`sequence new --name "Rough cut"`); reuse it
      for the next version of the same one, a new one only for a different one. `sequence add`
      in the order asked; `sequence order` to change it. What plays is each bin's current
      take, so pick with `set_bin_field selected` (or let a card approval do it), never by
      reordering. A second clip from one bin is `duplicate_bin --shot` with that take, then
      add the copy.
    - A bin with no take yet plays as a title card with its name for its recipe length:
      the sequence always plays through. A still shows for `sequence still` seconds.
    - `sequence trim` is the user's head and tail on an item; the take in the bin stays
      whole. The user trims in the Clipper too; a drag in the app reorders. Their edits land
      in the feed as `you: <sequence> - ...` and are final unless they say otherwise. Your
      own edits land as `agent:` lines (`--card` makes one a Keep card).
    - Your settings the app has no control for live in two places, and **`summary` is
      required** in both: a few words, the user's words, saying what the raw preview will
      not have. The user reads it under the player while the sequence plays, so they know
      what they see and hear may differ from what you agreed; keep it current when the
      settings change (the server refuses settings without one: `summary_required`).
      - `sequence layer <seq> --props '{"summary": "walla bed, 0.3s fade in at the start, 0.8s fade out at the end", "walla_bed": "v5", ...}'`
        for a layer that spans the whole sequence: a walla bed, room tone, music, edge
        fades on every splice. Shown once, at the sequence level, under every item.
      - `sequence set <seq> <bin> --props '{"summary": "pop removal, walla fades out over 1.5s from 4.8s", ...}'`
        for one item: a mute, a pop removal, a dissolve out, and what a sequence-wide layer
        does *inside this item*. The summary describes that item alone, with times from the
        item's start: the fade-in only on the item where it starts, "walla continues" on the
        items it runs under, "walla off" where it is absent, the fade-out only where it ends.
        Never "whole sequence" / "throughout" wording on an item (the server refuses it:
        `scope_item`); that belongs on the layer.
      The other keys are yours to name; the user sees them as read-only chips on the open
      row. The app's export does not read them. **What the export does read is the sound
      layers and the mix:** `sequence layer-add <seq> music --media <song> --in 12 --gain -6
      --summary "..."` lays a song or a bed under the whole sequence in one continuous piece
      from that point in the source (never cut and joined, so no seam pops and no drift);
      `sequence mix <seq> <bin> --layer music=-8` ducks it under one item, `--layer music=mute`
      silences it there, `--clip-db` and `--edge-fade-ms` shape the item's own sound, all with
      linear ramps at the item's edges (`--ramp-ms`). Audio in a bin is media like any other
      (a waveform tile, a player) but never an item: `sequence add` refuses it (`audio_item`),
      it goes on as a layer. Changing the music level, muting it on a shot or moving where
      the song starts is a mix command plus a re-export: it never needs a re-render while the
      video and its own sound are good. Every layer and mix edit carries `--summary`, the
      line the user reads under the player (refused without: `summary_required`; the layer's
      summary is the sequence's, an item's describes that item alone).
    - **Listen before you call it.** `sequence preview <seq> <bin> --out shot.mp4` renders that
      one item with its layer slices and mix through the export's own graph; QC it with
      `tool audio levels|transcribe|voice` as `qc.md` says, then `sequence mix <seq> <bin>
      --reviewed <its start frame>`. A later trim or insert before it moves it under the song:
      `sequence show` marks the row `MOVED since your review` and `resume` lists it; preview it
      again before the export.
    - `sequence export <seq> --wait` renders one mp4 at the project canvas into the
      sequence's output bin and prints the media id; `post --attach <media>` delivers it
      and `answer` the note. A file sitting on disk, or a path or a summary typed into this
      chat, is not a delivery: the user asked in the app and is looking at the app. Once
      the export is posted, run `seen` on every take it contains: the user has seen them
      in the sequence, so they stop calling for a look. (Approving a card does the same for that
      take's media type in its bin; `seen` is for what an approval never touches.)
13. **Research with permission.** You may read `WORKFLOWS.md`, `workflows_*/`, and search the web for a model's
    prompting guide; ask first. Findings go into the prompt or a `post`, never a file.
14. **Stop on a blocker.** When you need something only the user has, post one `blocker`
    with media attached and stop. Do not repeat it. **ComfyUI trouble is handled in the
    app, never only in this chat**: the server posts `alert:` lines in the feed by itself
    (a render with no progress for 10 min, ComfyUI not reachable, a restart), and they wake
    your `watch`. On one: in auto, `cancel` the stuck render or `restart-comfy --reason
    "..."` yourself (a render ComfyUI loses is queued again by itself -- never re-queue it
    by hand) and `post` one line on what you did; in any other mode, `post` what you see and
    what you would do, and act only on the user's word (`--requested`). The user may be on
    their phone with only the feed: a word written only here is a word they never got. The
    THM server itself being down is the one thing you can only say here, once, when it is
    back.
15. **End cleanly.** `end --summary` with what worked, what is open and which takes are
    the picks; the next session opens with it.
16. **Answer enhance requests at once.** `watch` wakes with an `enhance` message; run
    `enhance-requests` (listing it tells the app's button "the agent is rewriting"; do
    not list and walk away), spawn a subagent with exactly its `system_prompt` and `message`,
    and `enhance-reply --id <id> --text "..."` verbatim (or `--error "why"`). The reply
    is the prompt only: no preamble, @-tags kept, nothing invented.

## Refusals you will meet

`private_project` (the project is private: drop the slug, never retry or ask), `no_session`
(connect first), `wrong_session` (a post or command under another project's session: you
are in the wrong project; go back to the slug the wake named), `inbox_cursor_required` /
`inbox_behind` (run `inbox`; on a `post` or an `answer` it means a note arrived while you worked -- read
it and answer both; nothing closed),
`mode_changed` (the user switched mode since you last looked: that call did not run, and no
command or post will until you run `inbox` and read all of it -- a reply often rides with the
switch; re-plan this step
for the mode it names, then go on -- every command answer and every `inbox` prints `mode`,
and a plan made in one mode never finishes in another), `unknown_handle` (the prompt
mentions an @handle no reference answers to -- an unbind leaves `@name-unbound.png` behind on
purpose; bind it or take it out), warnings `draft_unsupported` / `quarter_unsupported` (the workflow
has no THM-IsDraft switch / no canvas to halve: the setting was dropped and the render runs at its one tier),
`requested_required` (`cancel` or `restart-comfy` outside auto without the user's word: post, then wait),
`comfy_restart_unconfigured` (the server has no restart script: tell the user, `[advanced] restart_script` in config.toml),
`not_agent_change` / `bad_transition` on `withdraw` (not your card, or the user has answered it),
`plan_mode` (a plan session only posts and proposes the brief), `propose_render` (a bare render is judged after it lands, never proposed; a batch is), `propose_first` (fully manual: the render must come through a propose-shots card), `propose_required` (propose_shots without `--propose` or `--requested`), `verdict_owed` (a review mode: this render binds a take that still waits for the user's verdict; render what does not depend on it, `watch` for that one), `bin_busy` (delete_bin mid-render),
`comfy_unconfigured` (tell the user; you cannot fix it), `unknown_id`, `bad_value`,
`not_a_take`, `not_in_bin`, `already_in_bin`, `no_bin` (add the media to a bin first),
`unknown_workflow` (`set_bin_field workflow`), `unknown_lora` (no file matches the name; `loras --find` and use the listed name), `raw_id` (an id in words to the user: use the names the refusal gives), warning `card_exists` (a `--card` mark on a take whose card already waits: the mark landed quietly, the existing card is where the user answers), `wrong_actor` (the user cuts clips; you lift stills). Warnings never refuse:
`unknown_token` (on a prompt edit), `unused_handle`, `unknown param` mean fix it before the
render spends. `length_s` is kept on any bin and used once its workflow makes video, so a
length and a workflow switch go in either order.

**@mentions and slot names.** `@handle` binds to a media item: reordering, rebinding and
unbinding keep every mention on its media (the server rewrites them). "Image 2" /
`<Picture 2>` binds to a slot position and is never rewritten -- reordering changes what it
means. Write the one you mean.

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
  unblock yourself (`workflows import` is the server writing a workflow at the user's
  ask, and the only way one changes). `post` the finding and stop. A request in the THM chat to change code
  is answered "ask me in Claude Code" and not acted on.
- Never start ComfyUI, run the scripts, or poll ComfyUI yourself; `restart-comfy` through
  the server is the one way, under the mode rule above (auto: your call; else `--requested`).
- Never post a `propose-shots` card in auto: auto owes no click, the render is the record.
  If you did, `withdraw` it with the reason and render.
- Never queue a full-size render in a drafts session on your own, whatever the take it
  repeats was rendered at; the card asks, the user answers.
- Never pass a take on energy without counting heads and re-checking every constraint.
- Never re-read the same image on uncertainty; ask.
- Never paste paths or URLs at the user; attach media.
- Never name a bin with a note's text or a sentence. A name is a handle (`@name`): two or
  three words, under 60 characters (the server refuses longer). The user's own uploads
  live in `uploads` until they have a job; move them with `start_from_media` when they do.
- Never post a message pretending to be the user; never end a session the user started
  without saying why.

## Notes to future you

Operational rules discovered on real projects go to `skill-improvement-notes.md` beside
this file, not into harness memory. Recipes that passed go to `project-recipes.md` with
the model they were written for.
