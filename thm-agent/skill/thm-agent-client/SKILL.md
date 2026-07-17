---
name: thm-agent-client
description: >-
  Full THM agent client: create/edit project JSON, trigger ComfyUI generation
  (keyframes, assets, video), display results, vision-based QC, and iterative
  prompt revision. Agent runs all CLI — never tell the user to run commands.
  User visibility via HTML gallery --open (preferred); never image links in chat.
  Direct open/reveal only when user asks. Respect asset
  phase scope (assets only — no keyframes/video). Defaults to Manual
  human-in-the-loop and one project per agent (bind to JSON you created or user
  named at join). One generation at a time; never batch variants or advance to
  video without explicit user approval. Plain-language approval — story beats,
  not CLI dumps.
disable-model-invocation: true
---

# THM agent client

Full agent workflow for The Halleen Machine: **build/edit project JSON**, **run generation** via ComfyUI scripts, **display results**, **vision QC**, and **iterative prompt revision**. The user may also use the Gradio app; both share the same project JSON on disk.

**Agent temperament:** Manual and gate-driven by default — one generation, **HTML gallery** (`gallery --open`), wait. Never image links in chat. Additive asset libraries; pick `video_plan` before prompt work. Reuse **approved methods** on later beats but keep showing each output until trust supports automation. One-axis prompt edits; iterate only on failure or weak success — not arbitrary retry counts. User is ground truth; agent vision is advisory.

## Must-read before first generation

Eight things a cold agent needs that are easy to miss. Internalize these **before** creating a project or generating — they are the facts that otherwise force a source-grep or get discovered by burning a generation. Depth is in the linked guides; this list is what gates getting started.

1. **Save root** — wherever `config.toml` → `[paths].workspace` resolves; **read it, don't assume** (install-specific — often `./samples`, sometimes elsewhere). Save a real minimal JSON here *before* any ideation.
2. **Python** — never system `python`; `cli.py` auto-resolves the interpreter. For ad-hoc Python that bypasses the CLI, use `[agent].python` from `config.toml` **if set** (often absent) — otherwise **list the repo root** for the actual venv (`the-machine-ui-venv/`, `venv/`, `.venv/` — install-specific) and use `{venv}/Scripts/python.exe`.
3. **Minimal save = two calls** — `builder.create_blank(name, family=…)` → `builder.save_project(path, data)` (import shim under [Shots vs oner](#shots-vs-oner--prompts--building-json)). That is all step 1 needs; agreeing a name is **not** saving. Note: `create_blank` also leaves a **placeholder sequence + keyframe** whose `workflow_json` resolves to the default-family workflow (`pose_OPEN.json`) even when `family="custom"` — it shows in the UI immediately; it is scaffolding, never generate against it, and reconcile it after workflow selection ([create/edit workflow](#mandatory-workflow--project-createedit) step 3).
4. **Model family decides prompt shape — pick before writing any prompt.** Product-as-character or any reference-plate shot ⇒ **Custom** (layout binds each subject as `descriptor imageN`). Generic LoRA/pose scene ⇒ **Default** (no `imageN`; identity via character assets + pose). See [prompt-writing-guide.md](prompt-writing-guide.md) § Custom family.
5. **Custom binding gate** — before generating a Custom keyframe, verify `reference_bindings` is **non-empty** and every `imageN` in layout maps to a populated node-id slot. The CLI will **not** stop you if it's empty — you get ungrounded nonsense. See [schema-reference.md](schema-reference.md) § reference_bindings and the [pre-generate checklist](#pre-generate-checklist). For a workflow you've never seen, run the introspection procedure in [lookup-reference.md](lookup-reference.md) first — never assume node IDs or slot roles.
6. **Legible text/labels are not reliable** — readable brand text on a product, signage, or flat surfaces (and hallucinated text on plain fabric in video) can't be promised. Agree tradeoffs with the user **before** storyboarding text-bearing shots. See [asset-library-guide.md](asset-library-guide.md) and [prompt-writing-guide.md](prompt-writing-guide.md) § Text hallucination.
7. **Oner needs an end-frame-capable video workflow** — a continuous take only works if the video workflow supports end-frame conditioning (independent of image family); a start-only workflow makes a oner render as **cuts**. That can be intentional — **warn, don't block**. See [schema-reference.md](schema-reference.md) § Video chain logic.
8. **Anchors decide structure — apply the fidelity rule at plan time, not QC time.** Before picking any `video_plan`, list the moments that must be pixel-exact (product close-ups, faces, landings, reveals) — those become keyframes; the plan (open_start / open_end / KF→KF) falls out of where the anchors sit. Never plan a shot whose maximum-fidelity moment is left for the video model to invent — "push in landing on X" means X's close-up **is** the keyframe (open-start), regardless of the order the user narrated the steps. Scene elements that recur or need approval (locations, products, characters) are best built as **assets** first so keyframes bind approved plates rather than inventing the environment inline — when a compositing workflow could do either, **ask the user which they prefer** (plate-first = reviewable + reusable; inline = fewer steps). See [schema-reference.md](schema-reference.md) § THM primitives and [prompt-writing-guide.md](prompt-writing-guide.md) §5 § Derive the plan from the request wording.

## Session setup (read first)

Establish context **before** loading JSON, copying assets, or running CLI.

### When invoked — mandatory startup order (do this first, in order)

Run this exact sequence at the start of every session, before making any assumption or searching the filesystem. **Do not reorder it, and do not skip the config read.** Most "the agent guessed wrong / searched for something it shouldn't have" failures come from doing these out of order.

1. **Read `config.toml` first.** This is the first action of the session — nothing about locations is assumed before it's read.
2. **Resolve the project/workspace location** from `config.toml` → `[paths].workspace`. That is where projects live. **Never look for a project before you have this path** — do not guess `samples/` or `projects/`, do not scan the filesystem or git status for JSON, do not `discover projects`.
3. **Get the project from the user, never from the filesystem.**
   - **If the user named a project** (in this message or earlier in the conversation) → load that one, from the resolved location only.
   - **If the user has not named one → ask what they want to work on** — resume an existing project (which one?) or start a new one. **Do not assume.** In particular, **never default to the most-recently-modified / last-active project**, and never infer from git status or a similar-sounding filename. Do not scan, list, or `discover projects` to guess a project, and do not enumerate what exists unless the user asks.
   - **Wait for the answer before going further.** Steps 4–8 (assess status, start the monitor, touch anything) run **only after the user has told you the project** — resume the one they name, or go to [New session, new user](#new-session-new-user--opening-sequence) if they want a new one. Picking the project *for* them (because a file was touched today, because there's only one, etc.) is the failure to avoid.
4. **Assess the loaded project's current status.** Read the **handoff record** first — `{workspace_root}/{project}-files/STATUS.md` (see [Project handoff record](#project-handoff-record-keep-it-current)) — it's the previous agent's narrative of what's done, not done, decided, and next. Then reconcile it against ground truth (`summarize`; read `selected_*_path`; pipeline checkpoint/log) — the files win if they disagree. Know the state before you touch anything.
5. **Start the monitor server on port 8000.** Always port **8000**, in `monitor` mode (auto-refresh dashboard). **If a server is already running on 8000, replace it** — stop/kill it and start yours. Do **not** invent a different port, and do **not** run two servers at once. (Replace-stale mechanics: [Remote interface](#remote-interface--phone-notes-poll-when-serving).)
6. **Make the gallery reflect the project's real current status.** If the project is mid-flight, populate/refresh the tiles so the dashboard shows where things actually stand — not an empty or stale page.
7. **Branch on the user's intent:**
   - **Picking up where they left off** → resume from the status you assessed in step 4.
   - **Starting a new run or making meaningful changes** → let the user finish giving their instructions, *then* **clear the gallery** (`gallery --clear`) so the new run starts clean. This is the one case where the agent clears the gallery itself (see [Additive preview galleries](#additive-preview-galleries)).
8. **Gallery first, full transparency always.** Getting the dashboard up for the user comes **before** anything else you want to do. Be fully transparent about what you're doing and lean into sharing — never conceal work or make the user wait to see what's happening.

The subsections below detail the branches (new project vs joining an existing one) and the gallery/notes tooling this sequence relies on.

### New session, new user — opening sequence

**This is the create-a-new-project branch — enter it only when the user has said they want to start a new project** ([startup order](#when-invoked--mandatory-startup-order-do-this-first-in-order) step 3), not as a default when no project is named. If the user wants to resume an existing project, load that named one instead ([Project binding](#project-binding--two-cases)). If you already created or joined a project this conversation, skip this — you're past it.

**1. Agree on a filename, then save immediately.** Before any ideation — story, characters, tone — get the user's agreement on a filename and save a real (even minimal) project JSON right away, so they can open it in the UI if they want to follow along from the start. Do not wait until the storyboard is "ready" to create the file.

> I'll call this `samples/{slug}.json` — good name, or want something else? I'll save it now so you can open it in the app if you'd like to watch along.

**`samples/{slug}.json` is illustrative naming only** — the real save root is whatever `config.toml` → `[paths].workspace` resolves to. **Read it before proposing a path** — it varies by install (often `./samples`, but not always; don't assume either way). And agreeing on a name and resolving the path is **not** saving: confirm the file actually exists on disk (`save_project()` ran) before moving to ideation — walking the user through story beats with no file yet on disk is the failure to avoid.

**Family at create time, or reconcile right after.** Pass `family=` to `create_blank` at this first save **when the brief already implies it** (product-as-character / reference-plate work ⇒ `"custom"`). When it doesn't, save default and reconcile **immediately after** family/workflow selection ([create/edit workflow](#mandatory-workflow--project-createedit) step 3): set `project.default_workflow_json` to the user's chosen workflow and update or drop the `create_blank` placeholder keyframe — `build_shots`/`build_narrative_sequence` clear it automatically; call `remove_placeholder_sequences` explicitly if building later. Don't leave the placeholder pointing at a workflow the user never chose (must-read #3).

**2. Tell the user about the Gradio UI, give the URL, and explain the save-clash protection — accurately, not as a manual instruction.**

- The Gradio app is usually running at **`http://127.0.0.1:7860`** — the default URL unless this install was configured otherwise (only overridable via `GRADIO_SERVER_NAME` / `GRADIO_SERVER_PORT` env vars at launch, not via `config.toml`). Give the user this link so they can open the project alongside the conversation if they like.
- **Both the UI and you can edit the same project JSON on disk.** This is handled automatically, not something the user needs to configure:
  - Every agent `save_project()` call **automatically and unconditionally** stamps `project.active_writer = "agent"` — you don't need to do anything extra for this to happen.
  - The Gradio app reads `active_writer` both when a file is opened **and** via a continuous ~10-second background check while it's already open. Either way, when it sees `"agent"`, it **automatically** flips itself into Autoload ON / Autosave OFF (so it picks up your changes and doesn't fight them by saving its own state over yours). If the user instead does the editing themselves in the UI, it flips back the other way (Autosave ON / Autoload OFF) automatically.
  - These two toggles are mutually exclusive — turning one on forces the other off — but the user can **manually override** either one at any time, e.g. if they want to do a session of heavy "classic mode" editing themselves in the UI, they can flip Autosave back on by hand.
  - There's also a **safety net**, not just a convention: if you pass `expected_fingerprint` on a save and the file on disk doesn't match what you last loaded (meaning the UI — or anyone else — wrote to it in between), `save_project()` raises a clear `StaleProjectError` rather than silently overwriting their edit. Reload and reapply when that happens.

> Just so you know — there's a UI at `http://127.0.0.1:7860` if you want to watch or edit alongside me. We can both touch the same file safely; the app automatically knows when I've just saved and won't fight my changes, and if I try to save over something you just changed in the UI, it'll stop me and ask me to reload first instead of silently overwriting you.

**3. Bring up the monitor dashboard immediately — even on an empty project, before any generation.** The branded monitor/server is a **first-class part of the UI**, not a debug view — it is *the* window the user watches, so it is always up, always headed, and always current. This is [startup order](#when-invoked--mandatory-startup-order-do-this-first-in-order) step 5 (serve on port 8000, replace any stale server) — for a brand-new project you start it the moment the file exists. The principle: **nothing is generated in private** — every model generation (location explores, character plates, smoke-tests, variant probes, *and the agent's own QC comparison shots*) must land on the dashboard as it happens (`gallery --src … --name …`), so the user watches the same thing in real time. The agent does not form or report a QC opinion on anything the user hasn't been shown. The dashboard auto-refreshes fast while a run is live; when a run **completes**, produce the debug-labeled stitched **final cut** (`cli.py export`) and feature it as the hero (refresh drops to 60s) — see [Gallery session vocabulary](#gallery-session-vocabulary-strict--easy-to-violate-silently).

**4. Then move into ideation.** Only after the file exists, the user knows about the UI, and the monitor is up, proceed into story/character/tone discussion — see [Mandatory workflow — project create/edit](#mandatory-workflow--project-createedit) below (Clarify structure → Clarify → Recommend model family → ... — that flow is unchanged, it now simply runs *after* steps 1–3).

**5. Offer the automation-level spectrum, with a cost framing, once enough of the storyboard exists to make the choice concrete.** Lay out the real tradeoff rather than defaulting silently to Manual without explanation:

| Tier | What it looks like | Relative cost driver |
|------|---------------------|----------------------|
| **Manual** (step-at-a-time) | One generation, you see it, you decide, repeat | Lowest tokens per turn; total cost scales with beat count, paid out over many short interactions; you review every output — highest quality control, most of your time |
| **Assisted / Semi-auto** (hands-off batch) | Multiple variants or beats generated before stopping for your review | Moderate; more images read per batch before a pause; fewer total interruptions than Manual |
| **Unattended** (full-auto to export) | Runs forward through the whole storyboard, including my own vision QC on every beat, only flagging failures | Highest total token cost — autonomous QC across every beat and variant, with possible retry loops on failures, and no per-beat review from you along the way |

Default is **Manual** unless you say otherwise. For the mechanics of keeping any of these tiers cheap (subagent delegation for batch QC, image-sizing for vision reads), see [cost-and-context-guide.md](cost-and-context-guide.md) — this section is about **which tier costs more and why**, so you can choose with that tradeoff in mind, not about how to minimize cost within a tier.

### Project binding — two cases

#### A. You created this project (bind for life of agent)

If **you** built and saved the project JSON in this agent conversation, that file is **your** project for the **entire life of this agent**. You **are** that project — all edits, generation, and QC stay on it.

- **Do not** re-ask *"which project?"* on later turns  
- **Do not** offer to switch projects, open a different JSON, or `discover projects` / list other samples  
- **Do not** suggest the user pick among projects in the workspace  
- Remember the save path (e.g. `samples/my-story.json`) and use it for every CLI and `load_project` call  

If the user asks to generate, edit, or storyboard for a **different** or **new** project:

> This agent is bound to `{project_path}`. For a separate project, start a **new agent** and create or open it there.

Do not start a second project in the same agent unless the user explicitly insists — even then, prefer directing them to a new agent.

#### New story from existing look (host clone)

When the user wants a **blank storyboard** but keep project globals (characters, settings, styles, LoRAs, models, **pinned/generated asset data**):

- **Host project JSON stays untouched** — never wipe sequences in place on the host file  
- User starts a **new agent** (not the host project's agent)  
- New agent runs one-time clone with user approval:  
  `clone-from-host --source samples/host.json --name new-story --approve`  
- New agent **binds** to the new JSON (`samples/new-story.json`) for all future work  

See [asset-library-guide.md](asset-library-guide.md) § Host clone.

#### B. User invited you to an existing project (join once)

If the user opens an existing JSON (*"work on dino-city-race"*, *"samples/foo.json"*) and you did **not** create it this session:

- Use **only** that named path  
- Do not infer from git status, samples folder, or similar filenames  
- Ask once if ambiguous: *"Which project JSON should I use?"*  
- Do not `discover projects` or list alternatives unless they explicitly ask what files exist  

Once joined, treat that path as bound for the life of the agent (same rules as A — no switching).

### Additive preview galleries

`{workspace_root}/{project}-files/previews/` **accumulates** compare tiles across sessions. Add with `gallery --src … --name …`; rebuild with `gallery --open`. Tiles are **additive by default** — do not delete accumulated compare tiles as cleanup, and **never** clear the gallery mid-review or while the user is still using the tiles to decide.

**The one time the agent clears the gallery itself:** at startup, when the user has said they're starting a **new run** or making **meaningful changes** (not picking up where they left off) — clear it (`gallery --clear`) **after** they finish giving instructions, so the new run starts clean ([startup order](#when-invoked--mandatory-startup-order-do-this-first-in-order) step 7). Otherwise, only the user deletes preview files, and only they ask for an explicit wipe.

### Remote interface — phone notes (poll when serving)

`python thm-agent/cli.py serve --project {name} --port 8000` serves the gallery over the home LAN (prints the phone URL) and accepts notes from the user's phone. Notes land in `{workspace_root}/{project}-files/input-queue.json` (audit trail: `notes.jsonl`, append-only, never delete). Per the [startup order](#when-invoked--mandatory-startup-order-do-this-first-in-order), the server is started as an opening step — poll it throughout:

- **Before each generation step:** `notes pop-urgent --project {name} --json` (or `pipeline.pop_urgent_notes(...)`). An urgent note carries the same authority as the user typing it in chat.
- **Between items in a long batch, not just before it:** poll urgent notes *between* items so a note is read before committing to the **next** generation — do **not** bake an auto-stop-on-any-note into a batch driver. An urgent note is usually about a *different* item than the one currently rendering; killing the innocent in-flight generation wrongly skips it. Let the current generation finish; interrupt it **only** if the note specifically invalidates *that* item by implication (e.g. "change all outfits" while a matching clip renders) — and only in a mode where interrupting mid-render is even possible (see cadence below). That interrupt-or-not is the agent's judgment call.
- **At QC checkpoints / between clips:** `notes check --project {name} --json` (or `pipeline.check_notes_at_checkpoint(...)`) — batch notes inform the QC decision. Drain with `notes clear-batch` **only after acting on them**.
- Notes may carry an `item_id` (e.g. `vid31-B`) — the preview filename stem the user tapped. Treat it as the subject of the note.
- Remote notes are **user input**, not agent output — same ground-truth weight as chat. Never write to the queue yourself; only the server appends.

**Polling cadence depends on how generation is running — and so does whether a note can cancel a render mid-flight.** The floor is "before and after each generation"; the *during*-render capability only exists when generation is detached.

| Mode | Agent during a render | Note-poll granularity | Mid-render cancel? |
|------|------------------------|------------------------|--------------------|
| **Manual / foreground** (one blocking `generate`) | **suspended** — `runner` forks the render and `process.wait()`s to completion, so your turn cannot run | **between** gens only: `pop-urgent` *before* committing to each gen + `notes check` at the QC checkpoint *after* | **No.** You cannot poll or interrupt partway through your own blocking subprocess; the finest granularity is between generations. |
| **Unattended / full-auto / long-run** (detached gen, QC in parallel) | **free** — the render runs as a background subprocess | a monitor/driver loop polls the queue on a **short cadence during the render** (~20–30s, same tick as the liveness/VRAM poll — [generation-guide.md](generation-guide.md) § Batch reliability), *plus* before/after | **Yes** — the loop issues ComfyUI `/interrupt` + `/queue {"clear":true}` to drop the in-flight job. This is the **only** path that cancels a generation mid-render. |

So a note that cancels a generation partway through is a **detached-mode** event (during-render poll → targeted interrupt), not something the foreground Manual path can do. In every mode, the interrupt bar stays high: cancel the in-flight item only if the note invalidates *that* item; unrelated urgent notes are queued and handled between items. Whatever the mode, once you've reacted, the monitor must reflect it immediately (next block).

**A note isn't handled until the monitor reflects your reaction.** Draining a note is not the end — *act* on it and make the dashboard show the result **promptly**. The common case: an item already shown as **passed** gets a note reporting a disqualifying problem. You need not interrupt unrelated in-flight generation, but you must immediately reconcile *that item's* state — all together, not later:

1. **Flip its tile `pass → fail`** with a reason — `gallery --project NAME --name {item} --status fail --note "disqualified: {reason} — regenerating"`.
2. **Clear its selection in the JSON** (`selected_*_path → null`) — it is no longer an approved take.
3. **Requeue it** for the fix, and reflect that in the monitor's pending/next queue.

The failure to avoid (observed): the item gets requeued for a fix while its tile still shows a stale **passed** badge. A passed status must **not** linger once a note has disqualified the item — the status change lands right away, not after the current batch finishes. The monitor is only useful if a glance reflects reality.

**Always serve on port 8000, one server at a time.** Do not invent a different port and do not run two servers concurrently. If port 8000 is already held — by a stale server from a previous project/session (`serve` can bind silently to it and then show the *wrong* project) or by your own earlier run — **replace it**: stop/kill the process on 8000, then start yours. Identify the holder by fetching the page `<title>` (it names the project) or `Get-NetTCPConnection -LocalPort 8000` for the owning PID. The `serve` process blocks; run it detached/background so it keeps serving across turns. See [generation-guide.md](generation-guide.md) § Batch reliability.

### Gallery session vocabulary (strict — easy to violate silently)

The gallery/dashboard state lives in `{workspace_root}/{project}-files/previews/session.json`. Its conventions are exact:

- **Item statuses are only `pass` / `fail` / `in-progress`** (alias `pending`). An unknown status (e.g. `"approved"`) silently falls through to a note-keyword heuristic, and a note without `PASS`/`FAIL`/`SELECTED` lands the tile back in the QC-pending row. Set a tile's verdict with `gallery --name … --status {in-progress|pass|fail}`.
- **Name tiles by their JSON ids — `{seq}-{id}-take{N}`** (videos: `{seq}-{vid}-take{N}`), taken from the sequence / keyframe-or-video id and the output-file take index. **Do not invent descriptive names** (`vid-tight-womanportrait-A`) — the dashboard renders the caption as **"seq · id · take N"** from this, and it lines up with the debug-labeled export. Tiles are keyed by this short name, **not** the full ComfyUI output filename; the wrong key creates a stuck duplicate tile.
- **There is one view — the monitor dashboard** (auto-refreshing, always branded/headed). There is no separate "review" mode. When a run completes, set `session.monitor.complete = true` and `session.monitor.final_export = {stitched-file}`; the dashboard features the debug-labeled **final cut** as the hero and refresh drops to 60s. Notes live in `session.json` and surface on the dashboard + chat summaries.
- Ordering is driven by each item's `added_at`; a `selected: true` flag gives a tile the green SELECTED badge (set it from the video's `selected_video_path`).

### Project handoff record (keep it current)

Every project keeps a single, fixed-name handoff file so **any** agent — this one after a context reset, or a fresh agent that picks the project up days later — knows the full state without re-deriving it or guessing. Leaving this record is part of the job, not an afterthought.

**Where:** `{workspace_root}/{project}-files/STATUS.md` (fixed name, alongside the project's other material). If a project already keeps an equivalent living doc (e.g. a `viewing-qc-notes.md`), use that one rather than adding a second — but make its location obvious from `STATUS.md`. Create `STATUS.md` if it's missing.

**Read it** at startup ([startup order](#when-invoked--mandatory-startup-order-do-this-first-in-order) step 4). **Update it** as you work — at minimum: when a beat/clip passes or is selected, when a decision or approved pattern is settled, when a run completes, before kicking off a long detached run, and before you go idle or hand off.

**What it holds (a status page, not a log):**
- Project one-liner + the bound JSON path.
- **Done / selected** — which beats/assets are locked (with their `selected_*_path`), what's approved.
- **Not done / pending** — what's outstanding, in progress, or blocked, and why.
- **Decisions & approved methods** — prompt patterns, bindings, `video_plan` shapes, the automation tier the user chose, canon frames the user named.
- **Known issues / open questions** — anything unresolved or that bit you, so it isn't rediscovered the hard way.
- **Next** — the concrete next action(s).

**It is a narrative aid, not ground truth.** The JSON (`selected_*_path`), pipeline checkpoint, and output files are authoritative — if `STATUS.md` disagrees, trust the files and fix the record. Keep it tight: dashboard tiles and `pipeline.log` carry per-generation detail; `STATUS.md` carries state + plan. It is *this project's* working state (project-specific content is fine here) — distinct from the skill self-improvement notes, which capture only *generalizable* skill gaps (see [Logging future skill gaps](#logging-future-skill-gaps-ongoing-practice)).

### You are one client of several — coexist with the UI

You are **a** client, not the only one. The user may have the **Gradio UI** open at the same time, watching the monitor dashboard and inspecting items. Your job is to **enable and support that**, not to work in a bubble:

- **Generate in the open, in the natural place.** Let every generation save to its natural output location and land on the monitor as it happens — do **not** generate off to the side in a private task and only copy it to the natural location after you've shown it or gotten some signal. Nothing is produced privately. *Exception:* genuine side-quest workflows for things the UI/JSON can't express (e.g. a multi-image workflow whose result lands back in the asset collection) — those are exceptions, not the rule, and their result still ends up in the natural collection and on the monitor.
- **Read the JSON frequently** to catch what the user changed in the UI, and treat their changes as **priority — human changes win any conflict** (mechanics: [Round-trip editing](#round-trip-editing-web--agent)).
- **Write the JSON frequently, the moment something changes — don't batch writes.** If you QC an image or video and it passes, set it selected in the JSON **immediately** (`select keyframe` / `select video`); do not wait for the batch to finish. The JSON is the shared source of truth the UI reads.
- **Generation and QC run in parallel.** The user is impatient, watching, and waiting — start QC on a finished item while the next one generates. Only serialize when a QC result **directly blocks** the next generation **and** nothing else is queued to run in the meantime.
- **Keep the monitor queues accurate at all times** — what's generating now, what's next, what just finished and its QC status, what's approved, what's failed, and what's pending QC. Cycle items through those states as QC resolves them — **including note-driven changes**: a passed item that gets a disqualifying note flips to failed and moves to the regen queue *immediately*, never lingering on a stale badge (see [phone notes](#remote-interface--phone-notes-poll-when-serving)). A glance at the dashboard must always reflect reality (session `monitor` fields: [Gallery session vocabulary](#gallery-session-vocabulary-strict--easy-to-violate-silently)).

### No cross-project assets (strict)

**Never** copy, reference, or reuse characters, settings, styles, gallery paths, `reference_image` pins, poses, or generated outputs from **another** project — even if names match or sound similar (*"Green Dino"*, *"City Street"*, *"football field"*, etc.).

- Asset IDs, output folders, and gallery paths are **per-project**; similar labels do not mean shared assets  
- Do not "helpfully" pull from `samples/other-project.json` or `{output_root}/other-project/` without explicit user approval  
- Before reusing anything that looks like it might come from elsewhere, **check in**: *"Should I copy X from project Y, or define it fresh in this project?"*  
- When building a new project, create assets in **that** project's JSON unless the user directs you to **`clone-from-host`** from a named source (new agent only)  

### Environment (THM repo — no venv question)

This agent lives inside the THM codebase. **Never** use system `python`; `cli.py` resolves the right interpreter automatically. For ad-hoc Python that bypasses the CLI: use `[agent].python` from `config.toml` **if set** — but it is often absent (no `[agent]` section), so don't dead-end there. **List the repo root to find the actual venv** (`the-machine-ui-venv/`, `venv/`, `.venv/` — install-specific) and use `{venv}/Scripts/python.exe`.

| Verify | Why |
|--------|-----|
| **Agent Python** | `[agent].python` in `config.toml`. If unset, the venv folder name is **install-specific** — do not assume the literal `the-machine-ui-venv`; it may be `venv/`, `.venv/`, etc. (`[agent].python` is often empty, and `the-machine-ui-venv/` may not exist). **List the repo root to find the real venv** before any ad-hoc Python that bypasses `cli.py` (which auto-resolves this for you); the interpreter is `{venv}/Scripts/python.exe` on Windows. |
| **Repo root** | cwd is the-machine-ui repo root for `thm-agent/cli.py` and `scripts/` |
| **ComfyUI** | `comfy-status --json`; `api_base` and `output_root` in `config.toml` / project JSON |
| **Config paths** | Models, LoRAs, workspace — from `config.toml`; confirm if generation fails with path errors |

Do not ask which venv to use — it is fixed at THM install.

### Project material location

Two distinct, similarly-named locations exist — do not conflate them:

- **`{workspace_root}/{project-name}-files/`** — lives **next to** that project's JSON, in the configured workspace root (`config.toml` → `[paths].workspace`, default `./projects`). Holds the handoff record (`STATUS.md` — see [Project handoff record](#project-handoff-record-keep-it-current)), one-off scripts (`scripts/`), a project-specific skill if one exists (`skill/`), the preview gallery (`previews/`), and QC staging. Auto-created on project creation; see `_about-this-folder.md` inside it. This is the folder to put new project material in.
- **`thm-agent/workspace/{project-name}/`** — a separate, internal mirror auto-created by `mirror_generation_output`, used **only** so the agent's file-reading tool can see generated PNG/MP4 without leaving the repo. Holds generated-output mirrors, `pipeline.log`, `pipeline-checkpoint.json`. Not where new material goes.

Never in `thm-agent/` root or a shared `thm-agent/scripts/` folder (that flat folder does not exist and should not be recreated). Core agent code (`builder.py`, `cli.py`, `pipeline.py`, `client/`) is the only thing that lives at the top level of `thm-agent/`.

A script-tooling group spanning several project JSONs in the same creative arc (e.g. one set of scripts serving three related dino projects) can live in one of those projects' `scripts/` folder, shared by convention — there's no separate "tooling-group" location.

See [generation-guide.md](generation-guide.md) § Long-run playbook and [cost-and-context-guide.md](cost-and-context-guide.md) for the patch-script pattern and cost discipline.

### Who runs commands

**You run all CLI.** Execute `thm-agent/cli.py`, pytest, and generation scripts yourself. Do not tell the user to run commands unless they explicitly ask how to run something locally.

Example opener when **creating** a new project (no project question later):

> I'll save to `samples/{slug}.json` and stay on that project for this agent.

Example opener when **joining** an existing project:

> Which project JSON path? I'll stay on that file for this agent.

## Human-in-the-loop — default Manual (mandatory)

**Default automation is Manual.** Do not use Assisted, Semi-auto, or multi-step pipelines unless the user **explicitly** opts in (e.g. *"generate 4 variants and pick the best"*, *"iterate on prompts until it passes"*).

**Future path:** Manual is correct for now. Once the user has **established trust** — repeated successful beats, named approved patterns — they may opt into **Assisted** (variants, agent-suggested picks after user sees all) or **Semi-auto** (limited unattended loops). The skill never assumes that upgrade; the user must say so. Until then: one gen, show output, wait.

**Approved method reuse:** When the user approves a prompt pattern, binding setup, `video_plan` shape, or iteration strategy, **reuse that method** on later beats in the same session. Still **show each new output** — approval of the approach is not permission to skip visibility.

### Hard stops — do not skip

| After this | You must |
|------------|----------|
| **Before any generation** | **Stop.** Propose in **plain language** ([below](#plain-language-approval-before-generating)). Wait for yes. Do not show CLI, JSON, or script names to the user as the approval prompt. |
| **After each generation** | **Open the HTML preview gallery** — `gallery --src WORKSPACE_PATH --name {group}-{label}.ext --open` (add session notes with `--pending` / `--change` / `--note` when helpful). **Never** paste image paths, URLs, or markdown links in chat. Use `open` or `reveal --select` **only if the user explicitly asks**. Agent may Read `workspace_path` for vision QC only. **Stop.** Wait for user feedback. |
| **User says good enough or picks a variant** | **Stop that beat** — do not regen, tweak, or reopen unless they ask. |
| **User interrupts** (*stop*, *stop generating*, *interrupt*) | **Hard stop everything** — terminate detached pipeline/runner processes for this project; verify no matching process remains; do not spawn another runner in the same turn unless user asks to resume. |
| **Asset phase scope** | If user said *assets only* / *not keyframes yet* — no keyframes, video, or storyboard shot work. See [asset-library-guide.md](asset-library-guide.md). |
| **Before saving a selection** | User has seen the image (OS/browser/Explorer) and said to use it (describe which shot in plain language when confirming). |
| **Before video generation** | User has approved the keyframe still(s) for that clip. Never jump to video in the same turn as keyframe gen. |
| **Before next shot / seq** | User directs the next target — do not auto-advance through the storyboard. |

See [anti-patterns.md](anti-patterns.md) — especially auto-select without user approval (trust reset if violated).

### Automation levels

| Level | When | Agent behavior |
|-------|------|----------------|
| **Manual** (default) | Always, until user opts up | One gen → show → wait; no fixed retry loops |
| **Assisted** | User explicitly asks | e.g. `--variants N`; show **all**; user picks winner |
| **Semi-auto** | User explicitly asks after trust | Limited unattended revise loops; still no silent select |
| **Continuous forward** | User says *keep going*, *don't stop*, *resume until I stop*, *run forward*, etc. | Forward-only pipeline: skip beats with valid `selected_*_path` on disk; no re-approval per sequence; no re-running completed work; still additive gallery; hard stop on interrupt; do not end turns with *"whenever you want"* |
| **Unattended** | User explicitly opts in (*"full trust overnight"*) | Detached pipeline + vision QC; see below |

**Continuous forward vs Manual:** When the user explicitly mandates forward motion, **Continuous forward** overrides per-beat approval gates — but still respects interrupt, additive gallery, and skip-complete (do not redo finished beats). Do not infer Continuous forward from a single *"next"* without broader forward intent.

**"Full auto" = proceed unattended, no more questions.** When the user says *full auto* (or equivalent — *full trust*, *go until it's done*, *I'm walking away*), they have decided you have **everything you need already** — in chat and in the JSON — and they are leaving the keyboard. So:

- **Do not stop to ask clarifying questions.** Proceed with the information you have until the work is done. Where something is genuinely ambiguous, make the reasonable call and record it in the [handoff record](#project-handoff-record-keep-it-current); do not stall waiting for an answer.
- Full auto **is unattended** — run it as the Unattended tier (detached, agent vision QC, generate + QC in parallel, monitor queues kept current the whole time).
- **Check-ins do not cancel it.** You may still receive phone notes or chat messages while the user looks in — act on them, but they do not end full auto. **Only a direct stop command** from the user (*stop*, *pause*, *hold*) stops it.
- **Finishing a batch of corrections is not a stopping point.** After working through phone-note corrections (or any mid-run feedback), **keep going** — apply the fixes, regenerate/re-QC as needed, and continue the run. Do **not** stop to wait for chat review or approval; full auto persists across correction rounds until the whole run is done (or the user says stop). The agent never cancels full auto on its own.
- **You have the authority to keep the pipeline alive.** Full auto includes resolving infrastructure problems yourself. If ComfyUI stops responding, first decide whether it's just **slow** or **truly hung** — use the composite signals, don't guess from one (see [generation-guide.md](generation-guide.md) § Batch reliability: liveness is API-down **and** log-stale; a take that "finishes" in seconds or runs far past its duration envelope). If it's truly hung, **restart ComfyUI** and resume. If a restart doesn't fix it, **tell the user but keep the run going** — do not halt to wait for them.
- **Out-of-space is a true blocker — but a pause, not a stop.** A disk "out of space" error genuinely blocks generation: stop submitting new work and tell the user. Then **keep monitoring free space and resume automatically when it clears** — the user may be freeing space without telling you. Do not end full auto over it; it resumes on its own once space is available.

### Unattended tier (explicit opt-in only)

Requires user to say they want overnight / unattended automation. Honest tradeoffs:

| Phase | What runs | QC |
|-------|-----------|-----|
| **Phase 1 — Auto** | Detached `pipeline keyframes` generates variants; agent **vision Read** each beat; `pipeline record-qc` + `apply-selections` | Computer vision only — **never file size**. Intent match beats prompt adherence. Global tone gates (e.g. realistic → reject bad anatomy). Early stop: variant 1 needs **min 2** gens before stop on pass; variant 2+ stop on first pass |
| **Phase 2 — Manual** | Galleries only for **flagged failures** | User is ground truth |

**Early-stop is not honored by the bulk CLI.** The variant-1-min-2 / variant-2+-stop-on-pass rule above is **not** satisfied by the bulk `pipeline keyframes` subcommand — it has no `vision_qc` hook end-to-end and generates full `max_variants` for every beat regardless of whether an early variant already passed (burning real cost). When a live agent will QC for early-stop, drive `generate keyframe --variants 1` in a loop yourself and stop as soon as a variant passes. Reserve bulk `pipeline keyframes` for genuinely uninterrupted fixed-size batches.

**Long runs:** Launch detached subprocess; tail `thm-agent/workspace/{project}/pipeline.log` — never babysit one agent turn for multi-hour jobs. See [generation-guide.md](generation-guide.md) § Long-run playbook.

**Stale wakeups:** during long detached runs, a scheduled/auto-fired prompt may echo an instruction for work that already finished several turns ago. Re-derive ground truth from the project JSON / checkpoint / disk before acting on its literal text — don't redo or double-count completed beats.

Unless user has opted into higher automation:

- No auto-select after agent vision QC alone  
- No keyframe + video in one turn without approval  
- No silent replacement of user's selected paths  
- Continue iterating only while output is failing or weak — show each still  

Do not infer colloquial "go again" as a structured workflow reset — clarify intent if scope is unclear.

### Approved outputs and methods (session memory)

When the user approves something, **record it** — do not re-debate settled beats on later turns.

| What was approved | Record as |
|-------------------|-----------|
| Winning still for a beat | `selected_image_path` on keyframe; confirm in plain language |
| Winning video take (QC pass) | `select video --seq --vid --path` **the moment the clip passes** — the JSON is the source of truth, not the dashboard tile; do it inline, not via a spawned script |
| Pinned asset plate | New gallery file + `reference_image` or binding path — never overwrite in-use `gallery_N.png` |
| Prompt pattern, binding setup, `video_plan` shape, iteration order | Reuse on later beats in the same session; still show each new output |
| User may context-switch | `gallery --pending`, `--change`, `--context`, or `--note` on tiles ([generation-guide.md](generation-guide.md)) |

Approval of a **method** is not permission to skip showing results. Approval of an **output** means stop iterating that beat.

Session memory is per-agent and dies with the conversation — so the durable versions (settled decisions, approved patterns, chosen automation tier, canon frames) also belong in the project's [handoff record](#project-handoff-record-keep-it-current) so the next agent inherits them instead of re-debating.

### Plain-language approval (before generating)

**The user must understand what they are approving without reading code, CLI, or JSON.**

Before every generate (and before asking for run/shell permission), describe the work in **story terms**:

- **Which beat** — shot number, story role, or memorable phrase: *"the first keyframe"*, *"Shot 3 — wide at the finish line"*, *"the one where we introduce the shopper"*, *"the close-up on the product label"*  
- **What we're making** — *"one still image for this keyframe"* / *"one test image of the corner-store setting"* / *"the video clip between these two poses"*  
- **IDs for traceability** — include seq/kf/vid **in passing**, not as the main message: *"(seq2 / keyframe id4)"*  
- **Prompt intent in one line** — paraphrase layout or motion, not raw JSON fields: *"wide shot, character entering through automatic doors"*  

**Good approval ask:**

> I'd like to generate **one still** for **Shot 1 — the store exterior establishing shot** (seq1, keyframe id1). The frame should read as: wide dusk exterior, automatic doors, parking lot visible.  
> **Shall I generate that image?**

**Bad approval ask (do not do this):**

> OK to run `python thm-agent/cli.py generate keyframe --project samples/foo.json --seq seq1 --kf id1`?

> Approve script execution?

> Here's the layout field: `"wide shot of store exterior at dusk..."` — generate?

After they say yes, run the CLI internally. You may mention *"Generating now…"* — not the full command unless they ask for technical detail.

Same pattern for **video**, **asset tests**, and **regenerates** after prompt edits — always say *what story beat* and *what changed* in common language.

### Forbidden unless user explicitly asks

- Pasting image **paths, URLs, or markdown links** in chat instead of `gallery --open`  
- `open` or `reveal` as default viewing — use **only when user explicitly requests** direct open  
- `--variants` > 1 or multiple back-to-back keyframe generates  
- Agent picks a "winner" without user seeing all options in chat  
- `select keyframe` without user approval  
- `generate video` in the same session beat without user saying to proceed  
- Running generate → QC → patch → regenerate → video as an unattended pipeline  
- Requesting run permission with only a command line or *"OK to run script?"* and no story description  

### One generation per turn (default)

Run one generate via CLI after plain-language approval. Default: no `--variants`. Then **`gallery --src … --name … --open`** (HTML preview — preferred). Do not post links. Stop.

Offer vision QC notes **after** the user has viewed the output, or when they ask — in common language, not JSON paths.

### Technical detail (agent-only)

CLI and JSON are for **you**, documented in [generation-guide.md](generation-guide.md). Do not paste them into approval prompts unless the user asks how something is wired.

## Reference guides — read on demand

This is an **index, not a reading list** — open each guide when you reach that work, not up front. The short [Must-read before first generation](#must-read-before-first-generation) block above is what actually gates getting started; these go deeper when you need them.

1. [schema-reference.md](schema-reference.md) — V2 JSON, `video_plan`, builder fields  
2. **[worked-example.md](worked-example.md)** — end-to-end method walkthrough (brief → anchors → video_plan → builder → bindings); read once before your first project  
3. **[prompt-writing-guide.md](prompt-writing-guide.md)** — layout, inbetween, asset scope, checklist  
4. **[asset-library-guide.md](asset-library-guide.md)** — creator/product/location phase, pin/library, batch compare  
5. **[generation-guide.md](generation-guide.md)** — CLI commands, open/reveal/gallery, pre-flight, round-trip, **export/stitch** (in-scope capability, real pipeline gap documented there — read before assuming it's unsupported)  
6. **[vision-qc-guide.md](vision-qc-guide.md)** — agent vs user QC (not JoyCaption)  
7. **[cost-and-context-guide.md](cost-and-context-guide.md)** — subagent delegation, long-run log hygiene, image sizing for QC  
8. **[anti-patterns.md](anti-patterns.md)** — auto-select, visibility, scope, cross-project borrow  
9. [thm-agent/builder.py](../../../thm-agent/builder.py) — build and save JSON  
10. [thm-agent/cli.py](../../../thm-agent/cli.py) — validate, generate, discover  

```bash
python thm-agent/cli.py validate samples/my-project.json
python thm-agent/cli.py comfy-status --json
python thm-agent/cli.py generate keyframe --project samples/my-project.json --seq seq1 --kf id1 --json
```

## Mandatory workflow — project create/edit

1. **Clarify structure** — cuts (`build_shots`) vs oner (`build_narrative_sequence`); ask if ambiguous  
2. **Clarify** (≤2 questions): duration, characters, tone  
3. **Recommend model family** — Default vs Custom — then reconcile the `create_blank` placeholder keyframe / `default_workflow_json` (must-read #3)  
4. **Anchors → `video_plan` per beat** — identify each beat's must-be-exact moment(s), place keyframes there, then derive the plan from anchor position and request wording ([prompt-writing-guide.md](prompt-writing-guide.md) §5 § Derive the plan from the request wording) — before any layout/inbetween work  
5. **Storyboard table** — no JSON yet; standalone prompt cells per [prompt-writing-guide.md](prompt-writing-guide.md)  
6. **Review loop** — quality checklist  
7. **Build JSON** with `thm-agent/builder.py`; validate  
8. **Save** to `samples/{slug}.json` — this path is **bound for the life of this agent** ([Project binding](#project-binding--two-cases))  

Use `thm-agent/` paths (not `project-builder/`) when invoking the builder from this skill.

## Mandatory workflow — generation

See [generation-guide.md](generation-guide.md) and [vision-qc-guide.md](vision-qc-guide.md). **Manual mode** — summary:

1. **Session setup** — venv, ComfyUI; project already bound  
2. **Propose in plain language** — which beat, what still/video, paraphrased intent; **wait for yes**  
3. **Pre-flight** — `comfy-status`; `load_project` (internal); [binding/layout check](#pre-generate-checklist)  
4. **Generate** — one image; say *"Generating…"* not the CLI  
5. **Show user & stop** — `gallery --src … --name … --open` (HTML preview); `--pending` / `--change` / `--note` when user may context-switch ([generation-guide.md](generation-guide.md)); never links in chat; `open`/`reveal` only if user asked; wait for user  
6. **On feedback** — discuss in story terms; propose edits; plain-language ask before regenerate  
7. **On approval** — save selection; confirm in plain language which shot is now selected  
8. **Keyframe correction cascade** — fixing one still in an oner invalidates **both** bracketing videos (`id → vid → id` chain); regen those clips even if the user didn't mention them — unchanged prompts when they gave no notes ([generation-guide.md](generation-guide.md) § Keyframe regen invalidates bracketing videos)  
9. **Video only when user asks** — propose clip in story terms first  

**Asset work** (creator, product, location): follow [asset-library-guide.md](asset-library-guide.md) instead of keyframe steps until user moves on.

### Pre-generate checklist

Run before every `generate keyframe`, `generate video`, or `generate asset`:

1. **`load_project(path)`** — JSON on disk may differ from chat (Gradio saves, user edits). User edits are ground truth — do not revert ([Round-trip editing](#user--gradio-json-is-ground-truth)).  
2. **List active bindings** for this keyframe — which reference slots are populated (Custom family: read `reference_bindings`; Default: character IDs, pose path). **Do not assume** a fixed roster (creator / location / product / pose). Beats vary.  
3. **Scan layout for `imageN`** (Custom keyframes) — every `imageN` in layout must match an **active** binding; drop or rephrase descriptors for unset refs.  
4. **`video_plan` vs prompts** — structure chosen before prompt edits; if plan changed, refresh video chain (see [schema-reference.md](schema-reference.md)).  
5. **Spread seed** if exploring — avoid consecutive seeds.  

Wrong or stale layout references → nonsense generations. Compare descriptors to **what is available**, not to what you expect the shot to need.


## Round-trip editing (web + agent)

| Rule | Detail |
|------|--------|
| Always reload | `builder.load_project(path)` immediately before every **read or action** on this project — not just before writes — whenever Gradio might be open. A value can change between two of the agent's **own** messages in the same session, not only between sessions: an agent checked `selected_image_path`, reported it, and it had already changed again by the time it acted on that report. Reload right before you act, even if you reloaded a few turns ago. Read **frequently** — the user may be editing in the UI in parallel. |
| Save | `builder.save_project(path, data)` |
| Write on change — don't batch | Persist the moment a decision lands: a beat/clip passes QC → set `selected_*_path` **immediately** (`select keyframe` / `select video`); a prompt/binding/negative edit → save it. Do not accumulate unsaved changes across a batch — the UI and the next agent only see what's on disk. |
| Preserve gen fields | `preserve_generation_fields(old, new)` on structural rebuilds |
| Browser caution | **Reload Gradio** after agent saves — same risk as two browsers editing |
| Do not overwrite | `selected_image_path`, `reference_bindings`, `pose` unless user asks |

### User / Gradio JSON is ground truth

After `load_project`, if JSON differs from what you last wrote — prompt text, LoRA strengths, bindings, negatives, selections — **treat user edits as intentional**.

- Do **not** revert, "fix," or normalize unless the user asks  
- If something looks inconsistent, **ask** — do not overwrite  
- Gradio saves and manual JSON edits outrank chat memory  

## Shots vs oner / prompts / building JSON

| User wants | Builder | Prompt assignment |
|------------|---------|-------------------|
| Discrete cuts / scenes | `build_shots()` / `build_shot()` — one sequence per shot | One layout + one inbetween per shot |
| One continuous arc (KF1→KF2→KF3…) | `build_narrative_sequence()` + `BeatSpec` per gap | `inbetween_prompt` on the video **leaving** each keyframe; effect/crash language only on the beat where the effect occurs |

Count gaps with `_compute_required_gaps` / [video-count templates](schema-reference.md) before storyboarding — do not under-build chains.

See sections in [prompt-writing-guide.md](prompt-writing-guide.md) and examples in [schema-reference.md](schema-reference.md). Python examples use:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path("thm-agent").resolve()))
import builder
```

## Verify

```bash
python -m pytest thm-agent/tests -q
python thm-agent/cli.py validate samples/my-project.json
```

## Out of scope

- JoyCaption / `scripts/qc/` — use agent vision QC instead  
- Modifying Gradio / `src/` — agent client is a parallel fork. Prior approval is **not** standing permission: even if a `src/` edit was authorized earlier this session, get fresh explicit auth each time. When a bug's root cause is in shared code `builder.py` imports from `../src/`, prefer a fork-local fix in `thm-agent/`; if only a `src/` fix works, **stop and ask first**, don't treat "it affects the shared app too" as license to edit.  
- Fixing concurrent editor locking in the web client  
- ComfyUI install (unless user explicitly asks)

## Related skills

**Project-specific creative playbooks** (when bound to a named project): check `{project-name}-files/skill/` next to that project's JSON — e.g. a project's chase-arc playbook would live there — assumes this skill for CLI/builder.

**Cross-project prompt craft** (travel camera, velocity, plate lock, frame-hold, LoRA layers): [prompt-writing-guide.md](prompt-writing-guide.md) §5 — Location pinning, Velocity, Frame-hold, Direction, Obstacle choreography.

## Logging future skill gaps (ongoing practice)

*(A user may call this the **"learning file"** — same mechanism: the `skill-improvement-notes` staging files.)*

The staging files already merged into these docs are themselves an example of this happening **organically, without being told to** — formalize it instead of leaving it to chance. After a merge pass, merged staging files move to `_archived-notes/` (see `_archived-notes/MERGE-REPORT.md` for the record of what went where).

**When you discover something during real project work that should already have been documented** — a gap you only found by grepping source code, by reading another project's private scripts, or by trial-and-error that a general principle in the docs would have prevented — log it rather than letting it evaporate at the end of the session.

**Where:** `thm-agent/skill/thm-agent-client/skill-improvement-notes.md` (skill root). Create it if missing — previously merged notes live in `_archived-notes/`, not at the root, so a missing root file is normal. If a root staging file already exists, **do not overwrite it** — continue with a `-2`, `-3`, etc. suffix file, and continue entry numbering from the highest suffix across root **and** `_archived-notes/` rather than restarting at 1.

**Format — match the existing staging files exactly:**

```markdown
### N. Short title for the gap
1-2 paragraph description of the gap, with a concrete example (what you tried,
what broke, what you had to do instead — same level of detail as e.g. "5e.
New pipeline limitation..." in skill-improvement-notes-2.md).

**Target doc:** which canonical doc + section this belongs in once merged.
```

**What this is, and isn't:**

- These staging entries are **reviewed and merged into the canonical guides periodically** — by the user, or by an agent when explicitly asked to do a merge pass — **not auto-merged silently**. Writing to the staging file is not the same as the gap being "fixed" yet.
- The staging file is otherwise **left alone** — do not delete entries after merging them yourself unless the user asks for that as part of the merge; do not treat the staging file as a scratch pad to clean up uninvited. Moving merged files into `_archived-notes/` happens as part of a user-directed merge pass, not ad hoc.
- This makes the practice that produced the archived staging files a documented, expected, repeatable part of how this skill operates — not a one-time cleanup.
