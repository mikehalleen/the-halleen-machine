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
| `select keyframe --project PATH --seq SEQ --kf KF --image ABS_PATH [--json]` | Persist approved selection |
| *(no `select video` verb exists)* | Marking a video as the approved selection is **not** a CLI command — set `sequences.{seq}.videos.{vid}.selected_video_path` directly via `data = builder.patch_field(data, "sequences.{seq}.videos.{vid}.selected_video_path", path)` (it returns a new dict — **reassign**, it does not mutate in place) or a raw dict write, then `save_project()`. Confirmed via `cli.py` argparse — only `select keyframe` is registered. |
| `gallery --project NAME [--open] [--src PATH --name FILE] [--note TEXT] [--pending TEXT] [--change TEXT] [--context TEXT] [--group KEY --group-note TEXT --group-pending TEXT]` | **Preferred** — HTML preview gallery; use after every generate (`--clear` is user-only) |
| `open PATH` | Open with default app — **only when user explicitly asks** |
| `reveal PATH [--select]` | Explorer highlight — **only when user explicitly asks** |
| `validate PATH` | Project JSON validation |
| `summarize PATH` | Storyboard text summary |
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

**Preview naming:** `{group}-{variant}.png` — grouped on the **first** hyphen (`keyframe-shot1-walk.png` → group **Keyframe**, three tiles in one row). Thumbnails ~52vh max, side by side; labels de-emphasized; click a tile to open full size.

**Additive gallery:** preview tiles accumulate. Add with `--src --name` (unique names per variant). **Only the user** deletes files from `{workspace_root}/{project}-files/previews/`. Agent must **not** use `gallery --clear`.

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

Groups and tiles sort **newest first**. The latest tile gets an orange **Latest** badge.

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

## Export / stitch (real pipeline gap — read before proposing)

`scripts/run_stitch.py` and `scripts/run_export.py` exist and are invoked the same way as `run_images.py` / `run_video.py` (`--config <project_json>`), but there is a confirmed architecture gap between them and the agent's video generation path:

- `run_stitch.py` requires either a `_lossless.mkv` companion file next to each video, or a legacy `frames_{idx}/` PNG folder.
- `run_video.py` only produces the `_lossless.mkv` companion when a `temp_frames_dir` exists during generation — and agent-triggered video runs do not have one. The agent's own generation log shows this directly:
  ```
  [LOSSLESS] Skipped - no temp_frames_dir
  ```
- **Net effect:** stitching an agent-generated project today produces **"0 frames"** for every agent-made clip.

**Before proposing stitch or export to the user:** check the relevant clip's generation log for the exact string `[LOSSLESS] Skipped - no temp_frames_dir`. If present, stitching that clip will not work via the normal path.

**Manual workaround (ffmpeg concat demuxer):** stitch the selected MP4s directly with ffmpeg's concat demuxer instead of `run_stitch.py`:

```bash
# 1. Build a concat list file (one line per selected_video_path, in sequence order)
# file 'C:/path/to/seq1_vid0.mp4'
# file 'C:/path/to/seq1_vid1.mp4'

ffmpeg -f concat -safe 0 -i concat_list.txt -c copy stitched_output.mp4
```

This re-encodes nothing (`-c copy`) and works on the MP4s that already exist on disk, bypassing the lossless-frames requirement entirely. It does not replace `run_export.py`'s other behavior (titles, audio mixing, etc. if any) — it is a stitch-only workaround.

**This is a product gap, not just a doc gap.** Either `run_video.py` needs to also emit a frames folder (or lossless companion) for agent-triggered runs, or this workaround should remain the documented path until that's fixed. Do not assume the gap has been closed without re-checking the log string above.

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
| Gen reports `"ok"/"success": true` but output looks stale or byte-identical across runs | `success` is derived from "newest file in the output folder" (`runner.py` `success=bool(main_path)`), **not** from the run's own result — a real ComfyUI failure (e.g. Windows paging-file `os error 1455`) can return success while pointing at a pre-existing file. **Check `result_lines` is non-empty** before trusting a result during unattended runs; the real error surfaces only in the `log` field, so never pipe generate `--json` through `tail`/`head` (it truncates `log`) |

---

## What not to use

- Do **not** call `scripts/qc/` or JoyCaption for QC — use agent vision ([vision-qc-guide.md](vision-qc-guide.md))  
- Do **not** modify `src/` or Gradio helpers — this client is a fork in `thm-agent/`  
- Do **not** use MCP/browser-in-IDE as the primary way to show outputs to the user
