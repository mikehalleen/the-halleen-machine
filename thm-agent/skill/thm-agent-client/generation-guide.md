# THM agent — generation guide

How the agent triggers ComfyUI generation via [thm-agent/cli.py](../../../thm-agent/cli.py). Invokes existing [scripts/run_images.py](../../../scripts/run_images.py) and [scripts/run_video.py](../../../scripts/run_video.py) — same contract as the Gradio UI.

---

## Pre-flight

### Scope and environment

**Project path:** Use the project you created in this agent, or the one path the user gave when joining. Do not switch files, list other projects, or run `discover projects` unless debugging paths the user named. If they want work on another project → suggest a **new agent**.

**Cross-project assets:** Do not borrow from other projects unless the user names the source and approves **`clone-from-host`** in a **new agent** session. Host JSON stays read-only.

**Environment (THM repo — fixed at install):**

- Never system `python`; the CLI auto-resolves the interpreter. For ad-hoc Python, use `[agent].python` from `config.toml` **if set**, else **list the repo root** for the actual venv (install-specific: `the-machine-ui-venv/`, `venv/`, …) → `{venv}/Scripts/python.exe`  
- Confirm **repo root** as cwd for `thm-agent/cli.py` and `scripts/`  
- Run `comfy-status --json` before generation  
- If paths fail, confirm `config.toml` (`models`, `loras`, `output_root`) with the user  

Always before generation (once project + venv are scoped):

```bash
python thm-agent/cli.py comfy-status --json
python thm-agent/cli.py validate samples/my-project.json
```

If ComfyUI is offline, stop and tell the user to start ComfyUI at the `api_base` in `config.toml` / project JSON.

Reload project from disk before edits:

```python
import builder
data = builder.load_project("samples/my-project.json")
```

### Pre-generate checklist (bindings + layout)

Before `generate keyframe`, `generate video`, or `generate asset`:

| Step | Action |
|------|--------|
| 1 | `load_project` — disk may differ from chat |
| 2 | List **active bindings** for target keyframe — do not assume creator/location/product/pose roster |
| 3 | Scan keyframe **layout** for `imageN` (Custom) — each mention must match a populated slot; rephrase or remove stale refs |
| 4 | If **`video_plan` changed** — refresh video chain; no orphan `videos[]` entries |
| 5 | **Spread seeds** when exploring — not 1000, 1001, 1002 |
| 6 | If a keyframe's **`selected_image_path` changed** (by anyone — agent, user, or Gradio) since the bound video was last generated, re-check that video's `inbetween_prompt` for object references tied to the **old** composition. A differently-composed replacement image can leave stale references (e.g. "papers on the desk behind him" when the new image has no desk) that nothing else catches automatically. |

See [SKILL.md](SKILL.md) pre-generate checklist and [anti-patterns.md](anti-patterns.md) lessons learned.

---

## CLI reference

Run from repo root. Prefer `--json` for agent parsing.

| Command | Purpose |
|---------|---------|
| `comfy-status [--json]` | ComfyUI reachable |
| `discover models\|loras\|workflows\|projects [--json]` | Local resources from config.toml |
| `images list --project PATH --seq SEQ --kf KF [--previews] [--json]` | List output PNGs for a keyframe |
| `generate keyframe --project PATH --seq SEQ --kf KF [--seed N] [--variants N] [--json]` | One keyframe still |
| `generate video --project PATH --seq SEQ --vid VID [--seed N] [--json]` | One in-between clip |
| `generate asset --project PATH --type character\|setting\|style --id UUID [--workflow WF] [--layout-override TEXT] [--json]` | Asset tab-style test gen |
| `select keyframe --project PATH --seq SEQ --kf KF --image ABS_PATH [--json]` | Persist approved keyframe selection |
| `select video --project PATH --seq SEQ --vid VID --path ABS_PATH [--json]` | Persist approved video selection (writes `sequences.{seq}.videos.{vid}.selected_video_path` with the same stale-write fingerprint guard as `select keyframe`). Write it **the moment a clip passes QC** — the JSON is the source of truth, not the dashboard tile. Equivalent internal path: `selection.set_selected_video(...)`. |
| `gallery --project NAME [--open] [--src PATH --name FILE] [--note TEXT] [--pending TEXT] [--change TEXT] [--context TEXT] [--group KEY --group-note TEXT --group-pending TEXT]` | **Preferred** — HTML preview gallery; use after every generate (`--clear` is user-only) |
| `open PATH` | Open with default app — **only when user explicitly asks** |
| `reveal PATH [--select]` | Explorer highlight — **only when user explicitly asks** |
| `validate PATH` | Project JSON validation |
| `summarize PATH` | Storyboard text summary |
| `export --project PATH [--format mp4] [--no-debug-labels] [--json]` | Stitch selected clips into one file — the run's **final cut** (debug labels on by default). See [Export / stitch](#export--stitch--the-runs-final-cut). |
| `clone-from-host --source HOST --name NAME --approve` | New project JSON with host globals; host read-only |
| `pipeline keyframes --project PATH [--variants N] [--force]` | Generate KF variants to checkpoint (no heuristic pick) |
| `pipeline record-qc --project PATH --seq SEQ --kf KF --image PATH [--rationale TEXT]` | Record vision QC selection |
| `pipeline apply-selections --project PATH` | Persist checkpoint selections to JSON |
| `pipeline status --project PATH [--json]` | Checkpoint + pending keyframes |

### Examples

```bash
python thm-agent/cli.py generate keyframe \
  --project samples/dino-city-race.json --seq seq3 --kf id3 --json

# Assisted only — user must explicitly request variants:
# python thm-agent/cli.py generate keyframe ... --variants 4 --seed 1000 --json

python thm-agent/cli.py generate video \
  --project samples/dino-city-race.json --seq seq1 --vid id1 --json

python thm-agent/cli.py generate asset \
  --project samples/dino-city-race.json --type setting \
  --id 8c2a73f6-50c1-4258-a925-3760cf9002af --json

python thm-agent/cli.py generate asset \
  --project samples/my-project.json --type setting --id UUID \
  --layout-override "straight-on product shelf, eye level" --json

python thm-agent/cli.py discover loras --json
python thm-agent/cli.py images list \
  --project samples/dino-city-race.json --seq seq3 --kf id3 --json
```

### Asset-test workflow resolution (when `--workflow` is omitted)

If `generate asset` runs without `--workflow`, the resolved workflow for Custom family comes from `workflow_for_asset_test()` → `resolve_project_default_workflow()` → reads `project.default_workflow_json` ([thm-agent/client/config_helpers.py](../../../thm-agent/client/config_helpers.py)). This is *the project's* default, not necessarily the workflow used for keyframe generation if those differ.

**Recommend passing `--workflow` explicitly** whenever the user is testing a specific workflow rather than "whatever the project defaults to" — otherwise an asset test can silently run against a different workflow than the one the user thinks they're validating.

---

## Display results (required — before anything else)

After **every** generation, **open the HTML preview gallery** for the user. Do not assume chat or an IDE preview pane displays images. **Never** paste image paths, `file://` URLs, workspace paths, or markdown image links in chat — the user cannot reliably view outputs that way.

### Viewing hierarchy

| Priority | Command | When |
|----------|---------|------|
| **Preferred (default)** | `gallery --project NAME --src WORKSPACE_PATH --name {group}-{label}.ext --open` | **Every** generate — single still, video, or batch |
| **On user request only** | `open WORKSPACE_PATH` | User explicitly asks for Photos / default app |
| **On user request only** | `reveal WORKSPACE_PATH --select` | User explicitly asks for Explorer |
| **Forbidden** | Paths, URLs, or links in chat | Never — not a substitute for opening the gallery |

**Default workflow after each generate:**

```bash
python thm-agent/cli.py gallery --project PROJECT_NAME \
  --src thm-agent/workspace/.../output.png \
  --name keyframe-shot1-walk.png \
  --note "Optional tile hint" \
  --open
```

For a new output on an existing gallery (additive tiles), always pass `--src` and a **unique** `--name`. To refresh the browser tab without a new file:

```bash
python thm-agent/cli.py gallery --project PROJECT_NAME --open
```

1. Read `workspace_path` from CLI JSON (preferred) or `main_path`  
2. **`workspace_path`** — agent **Read** for vision QC only; user views via **HTML gallery** in external browser  
3. **Stop** — wait for user feedback (Manual default)

Mirroring happens **automatically** on every `generate keyframe|video|asset` success.

### Visibility warning

- **HTML gallery is the preferred user viewing method** — branded compare page, session notes, newest-first  
- Chat, IDE preview panes, and **links in chat never work** as user QC — do not send paths or URLs instead of `gallery --open`  
- Windows may prompt for default browser — Chrome works well for HTML gallery (`file://`)  
- HTML gallery does **not** open inside an IDE preview pane; use external default browser  
- Agent `Read` of `workspace_path` is **agent-only** — not a substitute for opening the gallery

**Preview naming — use the JSON ids, don't invent names:** name every tile `{seq}-{id}-take{N}` (videos: `{seq}-{vid}-take{N}`), where `seq`/`id`/`vid` are the sequence and keyframe/video ids and `take` is the output-file index. The dashboard renders the caption as **"seq · id · take N"** from this, groups on the leading `seq`, and it matches the debug-labeled export. Do **not** invent descriptive names like `vid-tight-womanportrait-A` — the user reads the monitor in sequence/id/take terms.

**Additive gallery:** preview tiles accumulate. Add with `--src --name` (unique names per variant). **Only the user** deletes files from `{workspace_root}/{project}-files/previews/`, and the agent must **not** use `gallery --clear` during generation or review. The sole exception is the **startup new-run reset** (SKILL.md § startup order step 7): when the user is starting a new run / making meaningful changes, clear once after they finish instructions, then build the new run's tiles.

**Gallery tab reuse:** `gallery --open` writes `launcher.html` and opens `index.html` in a named window (`thm-gallery-{project}`) — repeated opens refresh the same browser tab.

**Session notes (memory aids):** hints persist in `{workspace_root}/{project}-files/previews/session.json`. Update with `--pending`, `--change`, etc. Use so the user can re-orient after slow gens or context switches.

| Flag | Scope | Example |
|------|-------|---------|
| `--pending` | Whole review | *"Pick shot 2 video or revise reach"* |
| `--change` | Latest batch | *"One hand holding phone — was two hands"* |
| `--context` | Session | *"Asset phase — no keyframes yet"* |
| `--group video --group-note` | All items in group | *"Lip movement forbidden"* |
| `--group video --group-pending` | Group decision | *"Pick best motion feel"* |
| `--src … --name … --note` | Single tile | *"Straight-on shelf test"* |
| `--name … --status {in-progress\|pass\|fail}` | QC status of one tile (works with or without `--src`) | flip a tile to `fail` the moment a note disqualifies it: `--name vid31-B --status fail --note "disqualified: extra dancer — regenerating"` |

Groups and tiles sort **newest first**. The latest tile gets an orange **Latest** badge. Set a tile's QC verdict with `--status` (drives the PASS/FAILED badge) — the moment QC resolves or a note disqualifies an item, update its status so the monitor reflects reality (see SKILL.md § Remote interface).

**Python helpers** (agent scripts / internal use):

```python
from client.workspace import (
    mirror_generation_output,
    add_preview_image,
    build_preview_gallery,
    open_gallery_in_browser,
    reveal_in_explorer,
    open_with_default_app,
)
```

```python
# Optional legacy helper (prefer automatic mirror from CLI)
from client.workspace import copy_to_workspace
copy_to_workspace(main_path, dest_subdir="generations")
```

Output layout (same as Gradio):

```
{output_root}/{project_name}/{seq_id}/{kf_or_vid_id}/*.png|*.mp4
```

---

## Propose before run (Manual default)

**Stop and ask in plain language.** Do not lead with CLI, file paths, or JSON.

Include:

| Tell the user | Example |
|---------------|---------|
| Story beat / shot | *"Shot 2 — character introduced at the entrance"* |
| What you're generating | *"one keyframe still"* / *"setting preview for the grocery interior"* |
| Brief visual intent | *"medium shot, person seated on bench, head bowed to phone"* |
| IDs (secondary) | *(seq1, keyframe id2)* — for your logs, not the headline |

End with a clear question: *"Shall I generate that image?"* / *"Want me to try this shot?"*

Execute CLI only after yes. While running: *"Generating Shot 2 now…"* — not the shell command.

Regenerate after edits: say **what you changed** in common language (*"I made the character taller in the character description"*) before asking to try again.

---

## Video prerequisites

Video generation requires `selected_image_path` on start/end keyframes. **Do not start video** until the user has seen and approved the relevant keyframe stills and explicitly asked for video.

Set selections only after user approval:

```bash
python thm-agent/cli.py select keyframe \
  --project samples/foo.json --seq seq1 --kf id1 \
  --image "D:/ComfyUI/output/foo/seq1/id1/foo_seq1_id1_001.png" --json
```

Then propose `generate video` as a **separate** step with user OK.

---

## Revise from feedback

1. Map feedback to the correct layer internally ([prompt-writing-guide.md](prompt-writing-guide.md))  
2. Tell the user what you plan to change in **plain language** before editing JSON  
3. Patch and save (internal)  
4. Propose **one** regenerate using the plain-language approval pattern  
5. Show new image; wait  
6. Repeat until user approves — **one axis per attempt**; only continue on clear failure or weak success ([prompt-writing-guide.md](prompt-writing-guide.md) §10)  

For structural rebuilds: `preserve_generation_fields(old, new)` before save.

When changing **`video_plan`**, refresh the video chain so `videos[]` matches gaps — do not leave orphan entries after plan edits.

### Keyframe regen invalidates bracketing videos

Oner sequences form an **`id → vid → id → vid → id`** chain. A video is anchored to **both** its start and end keyframe stills — *assuming an end-frame-capable video workflow* (a start-only workflow makes a oner render as cuts; see [schema-reference.md](schema-reference.md) § Video chain logic — warn the user, don't block).

**Use case:** User asks for a correction that requires one keyframe to be regenerated — even when they say nothing about the neighboring clips.

Example chain: `id1 → vid2 → id3 → vid4 → id5`

| User fixes | Also invalidated (automatic) | Usually unchanged |
|------------|------------------------------|-------------------|
| **id3** (middle) | **vid2** (ends at id3) **and vid4** (starts at id3) | id1, id5 — unless user flagged those too |
| **id1** (first) | **vid2** only (starts at id1) | id3, vid4, id5 |
| **id5** (last) | **vid4** only (ends at id5) | id1, vid2, id3 |

**Rule:** Regenerating keyframe **idN** clears `selected_image_path` on **idN** and `selected_video_path` on **every video where idN is `start_keyframe_id` or `end_keyframe_id`**. Do not leave bracketing videos selected — they were generated against the old still and are stale even if they looked fine in isolation.

**Prompts on bracketing videos:** If the user gave **no notes** on a invalidated video, regenerate it **unchanged** — same `inbetween_prompt`, negatives, duration, LoRAs — only the keyframe anchor changed. Patch the keyframe layout/inbetween only where the user flagged a problem.

Tell the user in plain language: *"Fixing the still at Shot 3 means redoing the clips into and out of that pose — I'll keep the motion the same on the ones you didn't mention."*

---

## Round-trip with Gradio

After any agent `save_project` or `select keyframe`:

> **Reload your browser** if the THM Gradio app is open. The app does not auto-reload JSON; concurrent edits can clash (same as two browser tabs).

The agent should always `load_project` immediately before editing.

---

## Multi-variant generation (Assisted only)

**Default: do not use `--variants`.** Only when the user explicitly requests multiple variants (e.g. *"try 4 seeds"*):

```bash
python thm-agent/cli.py generate keyframe ... --variants 4 --seed 1000 --json
```

Show **every** image in chat; wait for user pick; then `select keyframe`. See [vision-qc-guide.md](vision-qc-guide.md).

---

## Export / stitch — the run's "final cut"

When a run completes, produce a single stitched video with **debug labels** (each clip burned with its `seq:vid[:kf]` id) and feature it on the monitor as the hero:

```bash
python thm-agent/cli.py export --project samples/my-project.json --json   # debug labels ON by default
```

- `cli.py export` runs `scripts/run_stitch.py` over the project's `selected_video_path`s and returns the output path (`output_path` in the JSON). Use `--no-debug-labels` for a clean cut; `--format` for a non-mp4 container.
- **Local (non-partner) video workflows** already emit a `_lossless.mkv` companion per clip, which `run_stitch` uses for a high-quality stitch.
- **Partner-node (API) clips** (Seedance/ByteDance) return only an MP4 — no lossless companion — so `run_stitch` now **falls back to extracting frames from the MP4 itself** (in `frames_dir_from_selected_video_path`). Debug-labeled export therefore works for **every** workflow; the old "0 frames" gap and the manual ffmpeg-concat workaround are no longer needed.
- **On completion:** copy the exported file into `previews/` (e.g. `final-cut.mp4`), then set `session.monitor.complete = true` and `session.monitor.final_export = "final-cut.mp4"` — the dashboard features it as the hero and drops the refresh to 60s (SKILL.md § Gallery session vocabulary).

The debug labels use the same `seq:vid` vocabulary as the monitor's **seq · id · take** captions, so the export and the dashboard read consistently.

---

## Resource discovery

When the user asks about models, LoRAs, or workflows (not project switching):

```bash
python thm-agent/cli.py discover models --json
python thm-agent/cli.py discover loras --json
python thm-agent/cli.py discover workflows --json
```

Do **not** run `discover projects` or offer other sample JSONs — this agent is bound to one project. Paths come from [config.toml](../../../config.toml.example). Gallery/reference paths live in **this** project's JSON and output folders.

---

## Long-run playbook (Unattended + Continuous forward)

Multi-hour or multi-beat jobs must **not** run inside a single agent shell turn with repeated per-beat shell approval. See [cost-and-context-guide.md](cost-and-context-guide.md) for log-tailing and subagent-delegation discipline on these runs.

1. **Python:** CLI auto-resolves `[agent].python` from `config.toml` (or `the-machine-ui-venv` at repo root)  
2. **Detach:** One forward script chaining gen → select → video → gallery — avoids repeated shell-approval loops and duplicate runs  
3. **Log:** Tail `thm-agent/workspace/{project}/pipeline.log` and `pipeline-checkpoint.json`  
4. **Progress:** ComfyUI queue UI + `comfy-status --json` — not agent shell exit code  

### Forward pipeline discipline

| Rule | Detail |
|------|--------|
| **Skip-complete** | Before each beat, check JSON `selected_*_path` and that the file exists on disk — skip if both valid |
| **No re-kick** | Do not re-run sequences already complete in JSON |
| **Crash resume** | Read `pipeline.log` + JSON + output folders; resume from **first incomplete beat only** — do not restart from seq1 |
| **One runner** | Only one forward pipeline per project; on stop/interrupt, kill all matching processes and verify none remain |
| **QC gap (Unattended)** | If pipeline stops with pending keyframes, run vision QC (`record-qc` → `apply-selections`) before videos |
| **Force redo** | `pipeline keyframes --force` only when user asks to regenerate completed beats |
| **Targeted QC regen** | Clear the flagged keyframe **plus every bracketing video** (see [keyframe regen invalidates bracketing videos](#keyframe-regen-invalidates-bracketing-videos)); forward script skips the rest |
| **Ephemeral patch scripts** | Small `patch_*.py` scripts edit JSON + clear affected selections; prefer over hand-editing many fields repeatedly |
| **Additive gallery** | After batch regen, `gallery --src` named tiles for changed beats — never `gallery --clear` unless user asks |

**Patch script pattern:**

1. User flags beat in plain language (one axis if possible).  
2. Patch script updates layout/inbetween/negatives **only where the user flagged a problem** (keyframe layout, or that clip's `inbetween_prompt`).  
3. Set `selected_image_path` to `null` on the corrected keyframe(s).  
4. Set `selected_video_path` to `null` on **every video bracketing those keyframes** — inbound and outbound — even if the user did not ask to redo those clips.  
5. Run forward pipeline script (or `continue_*.py` project helper); bracketing videos regen with **existing** prompts when unchanged.  
6. Refresh gallery tiles for changed outputs.

Project-specific storyboard/patch helpers belong in `{project-name}-files/scripts/` (create the folder if it doesn't exist) — never in a shared `thm-agent/scripts/` folder or `thm-agent/` root. Optional one-offs, not a substitute for skills; document the **pattern** here.

**Portable paths (required pattern for new scripts):** `{project-name}-files/` and `{project-name}.json` are always siblings, by construction, no matter where the workspace root itself is configured — so derive both from `__file__`, never hardcode `"samples"`, `"projects"`, or any other workspace folder name. Finding `thm-agent/` is different: its distance from the script depends on where the workspace root is configured, which varies — anchor on **cwd** instead (scripts are invoked with repo root as cwd, same convention as this CLI itself).

```python
from pathlib import Path
import sys

# Self-relative: {name}-files/ and {name}.json are always siblings.
_FILES_DIR = Path(__file__).resolve().parents[1]      # {name}-files/
WORKSPACE_ROOT = _FILES_DIR.parent
PROJECT_NAME = _FILES_DIR.name.removesuffix("-files")
PROJECT = WORKSPACE_ROOT / f"{PROJECT_NAME}.json"

# CWD-relative, not __file__-relative — see note above.
sys.path.insert(0, str(Path.cwd() / "thm-agent"))
import builder  # noqa: E402
```

Never infer job completion from agent conversation timeout — ComfyUI keeps running independently.

### Batch reliability (ComfyUI health, VRAM, verification)

Hard-won rules for any multi-take run. All are content-agnostic and apply to every project:

| Rule | Detail |
|------|--------|
| **Verify by new output, never by exit status** | The runner's mtime guard (above) is the floor; in a driver, also record submit time and accept only files written after it. A take that "finishes" in a few seconds, or whose take number does not increment, is a silent failure — not a success. |
| **Read ComfyUI `/history`, never glob newest file** | Success detection must poll `/history` for the submitted prompt id and check `status_str`. Globbing the newest matching file finds the *previous* take and reports false success. Partner/credit failures surface here as `status: error` (e.g. "Payment Required"). |
| **Liveness = composite, not port alone** | A wedged listener can refuse connections while renders keep completing. Do not declare a crash on port-down alone: crash = API down **and** ComfyUI log last-write age stale (>~90s) and/or process gone. Log-mtime age is the reliable liveness signal. **Under [full auto](SKILL.md#automation-levels) the agent is authorized to restart ComfyUI** when it's truly hung (not merely slow) and resume; if the restart doesn't recover it, tell the user but keep the run going. |
| **During-render poll cadence (detached runs)** | Because a detached render doesn't block the agent's turn, a monitor/driver loop polls on a short cadence (~20–30s) *while the render is in flight*: `GET /queue` + `GET /system_stats` (liveness + VRAM high-water) **and** the note queue (`notes pop-urgent`). This is the only context where a note can cancel a render mid-flight — on an urgent note that invalidates the in-flight item, issue `/interrupt` + `/queue {"clear":true}`. The foreground Manual `generate` path blocks (`process.wait()`), so it has no such cadence — its finest granularity is between generations. (SKILL.md § Remote interface — Polling cadence.) |
| **Out-of-space is a pause, not a stop** | A disk "out of space" error truly blocks generation — stop submitting, tell the user — but under full auto keep monitoring free space and **resume automatically when it clears** (the user may be freeing space without saying so). |
| **`POST /free` between video takes** | Back-to-back video renders in one ComfyUI instance leak VRAM until inference can't fit (long hangs). `POST /free {"unload_models":true,"free_memory":true}` after every video take. On starvation symptoms, restart the process — a poisoned cache survives a retry. |
| **Serialize GPU work; flush after killing a client** | One generation batch at a time — launching a keyframe batch while a video batch renders can crash ComfyUI and fail both. Killing a hung client does **not** cancel its server-side job (orphan take files shift index numbering). After a kill: `POST /interrupt` + `POST /queue {"clear":true}`, or expect orphans. |
| **Driver contract: STOP file + per-clip reload** | A long driver should (1) honor a `STOP` file at `{project}-files/STOP` to halt between clips, (2) reload the project JSON fresh for every clip so prompt/keyframe patches land on not-yet-rendered clips, and (3) treat its run-status file (`todo`/`done`) as launch-time state — only edit it while the driver is stopped. |
| **Serve on port 8000 only — replace, don't relocate** | `serve` can bind silently to a **stale** server left on port 8000 from an earlier project — HTTP checks return 200 from the *old* gallery. The rule is one server on **port 8000** at a time: before serving, confirm 8000 is free or owned by *this* project (fetch the page `<title>`, or `Get-NetTCPConnection -LocalPort 8000` for the owning PID); if a stale/wrong server holds it, **kill it and take 8000** — do not invent a different port and do not run two servers at once. (SKILL.md § startup order + Remote interface.) |
| **Windows env-var scope** | A partner-API key (e.g. `THM_COMFY_API_KEY`) added to Windows **User** scope *after* the harness process started is invisible to spawned Python (`os.environ` empty), so jobs fail inside ComfyUI with "Unauthorized". Self-heal in a driver: if the var is empty, read `HKCU\Environment` via `winreg` and inject it into `os.environ` before generating. |

### Custom / partner video workflows (wiring gotchas)

When a project points at a non-default video workflow (partner API such as Seedance/ByteDance, or any custom graph), several silent failure modes exist — verify at pre-flight:

- **`video_model_family` must be `"custom"`.** Family `"default"` makes `resolve_project_video_workflow()` return the hardcoded default workflow and **ignore** `inbetween_generation.video_workflow_json` entirely — you can burn a full GPU job on the wrong graph with no warning.
- **Only the basename of `video_workflow_json` survives resolution** (`WORKFLOWS_DIR / Path(stored).name`). Per-clip baked workflow copies (duration/seed written in) must be written **into `workflows/`** with a distinctive prefix (e.g. `_tmp_{proj}_`) and cleaned up after — a path elsewhere resolves to a non-existent file or collides with a real workflow.
- **Partner nodes need duration and seed baked per clip.** The generic field-setters write only prompt/negative/frame-count/fps/width/height and find no seed target in a partner-only graph — so `duration_override_sec` and per-take seed variation never reach the node. Per clip: copy the template, write `duration` and a fresh `seed` onto the partner node's inputs, save to `workflows/`, point `video_workflow_json` at it.
- **End-frame support is per node set, not per tag.** Some partner node sets (e.g. ByteDance i2v) can only take a start frame; an end-frame-only (open-start) beat has no valid mapping and must be rewired to start-frame with mirrored motion. Partner workflows also don't auto-disconnect unused frame loaders — keep separate i2v and flf2v workflow files and pick per-clip wiring. (See [schema-reference.md](schema-reference.md) § Video chain logic.)

### Selecting the right file (mirror vs real output)

`select keyframe`/`select video` must point at the **real ComfyUI output path** (`main_path` from the generate JSON, or a glob of the real output dir). The workspace **mirror** copy has a different filename (it carries a `__seed{N}` suffix the real output lacks), so a path built from a mirror filename does not exist on disk. The failure is silent at select time and only surfaces later as a ComfyUI `LoadImage` 400 ("Invalid image file") when a video's start/end still can't be loaded. Treat `workspace_path` as agent-vision-only.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `THM agent Python not found` | Run `setup.py` or create `the-machine-ui-venv`; set `[agent].python` in `config.toml` |
| `ModuleNotFoundError: gradio` | Same — use `[agent].python`, not system Python |
| `UnicodeEncodeError` on `--json` (Windows) | CLI `_print_json` falls back to ASCII on encode error; subprocess should set `PYTHONIOENCODING=utf-8`. If `--json` print fails but generation succeeded, recover paths from output folder + `pipeline.log` — do not treat print failure as gen failure |
| User cannot see images in chat/IDE preview | Use `gallery --src … --name … --open` — never paste links |
| Agent posted path/URL instead of opening gallery | **Forbidden** — run `gallery --open`; links are not user QC |
| User asked for Photos or Explorer | `open` or `reveal --select` — only when explicitly requested |
| IDE embedded browser / MCP `open_resource` for images | Unreliable for user QC — use `gallery --open` |
| Too many tiles in HTML gallery | User deletes unwanted files from `{workspace_root}/{project}-files/previews/`; agent does not clear |
| `--layout-override` ignored | Must pass on `generate asset`; wired through `run_asset` → `prep_asset_run` |
| Gen reports `"ok"/"success": true` but output looks stale or byte-identical across runs | Largely guarded now: `runner.py` records a submit timestamp and only accepts an output file whose mtime is newer than the run, so a pre-existing file no longer counts as success — a stale result returns `success:false`, `main_path:null`, `is_new_output:false`, and a `stale_output:` line in `log` (ComfyUI likely crashed/offline — check `comfy-status`). Still **check `result_lines` is non-empty** and read `is_new_output` before trusting a result during unattended runs; the real error surfaces only in `log`, so never pipe generate `--json` through `tail`/`head` (it truncates `log`) |
| A `generate video` that "succeeds" in seconds (real renders take minutes) | Same class of silent failure: ComfyUI may serve a cached execution, or a client killed mid-render leaves the server job running. With the mtime guard a genuinely-cached return now surfaces as `stale_output`; if a client CLI was killed, the job keeps running server-side and the file lands later — poll the output folder for the next take index instead of re-submitting (re-submitting duplicates the render) |

---

## What not to use

- Do **not** call `scripts/qc/` or JoyCaption for QC — use agent vision ([vision-qc-guide.md](vision-qc-guide.md))  
- Do **not** modify `src/` or Gradio helpers — this client is a fork in `thm-agent/`  
- Do **not** use MCP/browser-in-IDE as the primary way to show outputs to the user
