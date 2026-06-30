# THM agent — lookup reference

Stable facts about this repo's tooling, written down once so the agent doesn't re-discover them via
`--help` calls and source-reading every session. Contrast with things that are **per-project or
per-install and must always be verified live** (see the callout at the end) — this doc is only for
facts that don't change between sessions.

---

## CLI command reference (`thm-agent/cli.py`)

All commands accept `--json` for machine-readable output. Run via the resolved agent Python, not
system `python` (see SKILL.md § Environment).

### Top level

```
cli.py {validate,summarize,comfy-status,discover,images,generate,open,reveal,gallery,select,clone-from-host,pipeline}
```

| Command | Args | Purpose |
|---|---|---|
| `validate` | `path` | Validate project JSON |
| `summarize` | `path` | Human-readable storyboard summary |
| `comfy-status` | — | Check ComfyUI API reachability |
| `discover` | `{models,loras,workflows,projects}` | List local resources of that type |
| `images list` | `--project --seq --kf [--previews]` | List keyframe images on disk |
| `open` | `path` | Open image/video with default OS app |
| `reveal` | `path [--select]` | Open folder in Explorer; `--select` highlights the file |
| `gallery` | see below | Build/refresh the additive HTML preview gallery |
| `select keyframe` | `--project --seq --kf --image` | Persist a keyframe selection |
| `clone-from-host` | `--source --name [--dest] --approve` | New project JSON with host globals; host file stays read-only |
| `pipeline {keyframes,record-qc,apply-selections,status}` | see below | Long-run pipeline + checkpoints |

**No `select video` subcommand exists** (only `select keyframe`). To select a video, set
`sequences[seq].videos[vid].selected_video_path` directly via `builder.load_project_with_fingerprint`
/ `builder.save_project(expected_fingerprint=...)`.

### `gallery`

```
cli.py gallery --project NAME [--open] [--clear] [--src SRC] [--name NAME]
               [--note NOTE] [--pending P] [--change C] [--context CTX]
               [--group G] [--group-note GN] [--group-pending GP]
```
- `--project` takes the **project name**, not the JSON path.
- `--clear` is **user-only** — agent must never call it (galleries are additive; see
  asset-library-guide.md § Never overwrite in-use gallery files).
- `--src`/`--name` add a new tile; the session-level flags (`--pending`, `--change`, `--context`,
  `--group*`) annotate the review session, not a specific tile.

### `generate`

```
cli.py generate keyframe --project P --seq S --kf K [--seed N] [--variants N]
cli.py generate video    --project P --seq S --vid V [--seed N]
cli.py generate asset    --project P --type {character,setting,style} --id ID
                          [--seed N] [--workflow W] [--layout-override TEXT]
```
- `generate keyframe`/`generate video` always reload the project from disk first — round-trip safe.
- `generate asset` is for asset-phase test generations (not full keyframes); `--layout-override`
  replaces the default identity-test framing (e.g. for shelf angles, poses) — see
  asset-library-guide.md § Layout override.

### `pipeline`

```
cli.py pipeline keyframes        --project P [--variants N] [--force]
cli.py pipeline record-qc        --project P --seq S --kf K --image IMG [--rationale TEXT]
cli.py pipeline apply-selections --project P
cli.py pipeline status           --project P
```
- `pipeline keyframes` is the **bulk, no-QC-hook** path — it always generates up to `--variants`
  (default 5) stills per keyframe with no early stop, because it's a detached subprocess with no
  live agent callback wired in. **Do not use this when you intend to apply live vision QC with
  early stopping** (see [SKILL.md](SKILL.md) § Unattended tier). For
  early-stop QC-in-the-loop generation, call `generate keyframe --variants 1` yourself in a loop,
  Read each result, and stop once a pass is confirmed (variant 1 needs a second generation to
  confirm before stopping; variant 2+ stops immediately on a pass).
- `record-qc` / `apply-selections` write to `thm-agent/workspace/{project}/pipeline-checkpoint.json`
  and then to the project JSON respectively — always run `apply-selections` after recording, or
  selections stay stuck in the checkpoint only.

---

## Builder API (`thm-agent/builder.py`)

Import pattern:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path("thm-agent").resolve()))
import builder
```

### Round-trip / persistence

| Function | Signature | Notes |
|---|---|---|
| `load_project` | `(path) -> dict` | Plain load + normalize |
| `load_project_with_fingerprint` | `(path) -> (dict, fingerprint_str)` | Use before any edit-then-save sequence |
| `save_project` | `(path, data, expected_fingerprint=None)` | Raises `StaleProjectError` if `expected_fingerprint` doesn't match disk — reload and reapply |
| `validate_project` | `(data) -> list[str]` | Empty list = valid. **Does not check prompt-style-guide compliance** — a clean validate is necessary but not sufficient; run the [prompt-writing-guide.md](prompt-writing-guide.md) §12 checklist against the prompt text separately |
| `patch_field` | `(data, dot_path, value) -> dict` | Surgical single-field update by dotted path |
| `preserve_generation_fields` | `(old, new) -> dict` | Merge helper protecting `selected_image_path`, `pose`, `reference_bindings`, `selected_video_path`, `reference_image` |

### Project / asset creation

| Function | Signature |
|---|---|
| `create_blank` | `(name, *, family="default") -> dict` |
| `add_character` / `add_setting` / `add_style` | `(data, name, prompt, *, negative="", lora_keyword="", generator_prompt="", generator_negative="") -> (dict, asset_id)` — note kwarg is `negative=`, not `negative_prompt=` |
| `clone_project_from_host` | `(host_path, new_name, dest_path=...) -> ...` — host file untouched |

### Sequence / keyframe / video building

| Function | Signature |
|---|---|
| `add_sequence` | `(data, *, seq_id=None, setting_id="", style_id="", setting_prompt="", style_prompt="", action_prompt="", open_start=False, open_end=True) -> (dict, seq_id)` |
| `add_keyframe` | `(data, seq_id, layout, *, character_ids=("",""), workflow=None) -> (dict, kf_id)` — **Default-family only** for `character_ids`/workflow auto-pick; Custom family needs `reference_bindings` wired separately — verify they are non-empty for every keyframe immediately after building a Custom project, before trusting any generated image as reference-grounded |
| `set_video_prompt` | `(data, seq_id, vid_id, inbetween_prompt, *, duration_sec=None, negative_prompt="") -> dict` |
| `build_shot` / `build_shots` | One sequence per discrete cut/shot. Takes `ShotSpec` |
| `build_narrative_sequence` | One oner — chained keyframes in one sequence. Takes `beats=[BeatSpec(...), ...]` |
| `remove_placeholder_sequences` | `(data) -> dict` — drop the empty seq from `create_blank` before building real shots |

**`BeatSpec` fields** (one beat inside a oner):
```python
layout: str
inbetween_prompt: str = ""
duration_sec: Optional[float] = None
character_ids: Tuple[str, str] = ("", "")   # Default-family only, see above
```

**`ShotSpec` fields** (one discrete cut):
```python
layout: str
inbetween_prompt: str = ""
duration_sec: Optional[float] = None
character_ids: Tuple[str, str] = ("", "")   # Default-family only
open_start: Optional[bool] = None
open_end: Optional[bool] = None
inbetween_prompt_out: str = ""              # second video, when both-open
duration_sec_out: Optional[float] = None
layout_end: str = ""                        # second keyframe, when both-closed (true KF->KF)
setting_prompt: str = ""
style_prompt: str = ""
action_prompt: str = ""
```

### Video-plan helpers

| Function | Signature |
|---|---|
| `recommend_video_plan` | `(layout="", inbetween_prompt="") -> (open_start, open_end)` — heuristic from motion-hint keywords |
| `resolve_video_plan` | `(layout="", inbetween_prompt="", open_start=None, open_end=None) -> (bool, bool)` — explicit values win, else recommend |
| `describe_video_plan` | `(open_start, open_end, keyframe_count=1) -> str` — human-readable gap summary |
| `normalize_clip_duration_sec` / `clip_duration_choices` | Whole-second clip length, clamped 1–10 |

---

## Discovering a Custom-family workflow's reference slots

Per [schema-reference.md](schema-reference.md), `reference_bindings` node-id keys are
**workflow-specific** and carry no semantic label in the workflow graph itself — every reference
slot is a generically-titled `LoadImage` node. To find a *new* workflow's available slots, don't
guess — extract them directly:

```python
import json
d = json.load(open("workflows/<workflow_name>.json", encoding="utf-8"))
for node_id, node in d.items():
    if node.get("class_type") == "LoadImage":
        print(node_id, node.get("_meta", {}).get("title"))
```

This is the API-export format (a flat node-id-keyed dict, not the `{"nodes": [...]}` UI-export
format — if `json.load` gives you a list under a `"nodes"` key instead, you're looking at the wrong
export type for this purpose).

To confirm which node-id maps to which `imageN` slot in the generation prompt (location vs
character vs style), the mapping is fixed **per workflow** but must be confirmed empirically — run
one generation with a known single binding active and read the `[REF] prelude` lines in the
returned `log` field (e.g. `"image2 is a character reference."`). Do not assume slot order is
sequential/renumbered based on which slots happen to be active — node-id identity is fixed.

---

## Asset selection model: sequence level vs keyframe level

Two distinct levels of selection:

- **Sequence level** (Gradio "Manage Project Structure" → select sequence → Properties → "Location"
  dropdown): picks **which asset** is present in / inherited by every keyframe in that sequence.
- **Keyframe level** (per-keyframe "Reference Images" panel, Slot 1–4): for assets active in the
  sequence, each keyframe independently picks **which specific library image** of that asset
  (`gallery.png`, `gallery_2.png`, …) to bind — applies to characters, locations, and poses alike.

Two keyframes in the same sequence can legitimately show different images of the *same* asset —
this is correct, intentional usage (e.g. to avoid two keyframes looking identical to the in-between
video model — see [prompt-writing-guide.md](prompt-writing-guide.md) § Reference-image variety for
in-between continuity), not an inconsistency to "fix."

---

## What NOT to treat as a stable lookup — always verify live instead

- **`reference_bindings` actual values for a given project** — workflow-specific node-ids, always
  read via a fresh `load_project()` call against that project's own file.
- **venv path / `config.toml` contents** — install-specific, not a repo-wide convention (the
  documented `the-machine-ui-venv` fallback may not exist on a given machine; list the repo root to
  find the real venv — see [SKILL.md](SKILL.md) § Environment).
- **Which gallery image is "currently selected"** for an asset or keyframe — the user or Gradio may
  have changed it since your last read; reload before acting (see schema-reference.md § Round-trip
  editing).
