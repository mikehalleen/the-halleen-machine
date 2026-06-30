# THM project JSON — V2 schema reference

Canonical example: [samples/THM-demo.json](../../../samples/THM-demo.json)

Full prompt rules: [prompt-writing-guide.md](prompt-writing-guide.md) · [docs/prompt_design.md](../../../docs/prompt_design.md)

---

## Top-level shape

```json
{
  "project": { },
  "sequences": { "seq1": { } },
  "sequence_order": ["seq1"]
}
```

- `sequences` is an **ID-keyed dict**, not an array.
- Order is `sequence_order` at root.

---

## `project` — globals + asset libraries

| Field | Purpose |
|-------|---------|
| `name` | Output folder name (required for save) |
| `characters[]` | Reusable character assets |
| `settings[]` | Location / environment assets |
| `styles[]` | Look / camera style assets |
| `style_prompt` | Global aesthetic for all generations |
| `negatives.global`, `negatives.keyframes_all`, `negatives.inbetween_all` | Merged at run time |
| `width`, `height` | Canvas size (default 1152×768) |
| `comfy.api_base`, `comfy.output_root` | ComfyUI connection |
| `keyframe_generation` | Image sampler defaults |
| `inbetween_generation` | Video workflow, duration, seeds — container for the sub-fields below |
| `inbetween_generation.fps` | Default **16** |
| `inbetween_generation.video_steps_default` | Default **14** |
| `inbetween_generation.video_workflow_json` | Absolute path in JSON; Project → Generation Defaults shows basename; rescans `workflows/` on load and Refresh Data |
| `inbetween_generation` — Slomo primer (`THM-SlowMoPrimer` tag) | Workflows may use `THM-SlowMoPrimer`; step count is fixed at **2** in `run_video.py` (`PRIMER_STEPS`), not UI-configurable |
| `inbetween_generation.express_video` | Rough Draft toggle; JSON default `false`; hidden from UI for now |

**Video LoRA tags (mutually exclusive modes):**
- **Single-path:** `THM-LoraAfterThisNode` or `THM-Lora`
- **Dual-pass high/low:** `THM-Lora-High` + `THM-Lora-Low` on each model chain (prompt LoRAs use `lora_pairs.csv` for high/low files)
| `image_model_family` | `"default"` or `"custom"` |
| `video_model_family` | `"default"` (locked `i2v_base.json`) or `"custom"` (BYO workflow dropdown) |
| `default_workflow_json` | Custom image family default workflow filename |

### Asset object

```json
{
  "id": "uuid",
  "name": "Display Name",
  "lora_keyword": "",
  "prompt": "Keyframe-facing text for composition / Klein supplement",
  "generator_prompt": "Optional Assets-tab-only positive prompt",
  "negative_prompt": "Keyframe-facing negatives",
  "generator_negative_prompt": "Optional Assets-tab-only negatives"
}
```

Optional: `reference_image` (gallery pin path).

**Assets tab Generate** uses `generator_*` when set; keyframe generation uses `prompt` / `negative_prompt` only.

---

## `sequences[seqId]` — storyboard unit

| Field | Purpose |
|-------|---------|
| `id`, `type` | `"seq1"`, `"sequence"` |
| `keyframes` | dict id → keyframe |
| `keyframe_order` | ordered keyframe ids |
| `videos` | dict id → video |
| `video_order` | ordered video ids (positional gaps) |
| `video_plan.open_start` / `open_end` | open boundaries for first/last video |
| `setting_id`, `style_id` | UUID refs into `project.settings` / `project.styles` |
| `setting_prompt`, `style_prompt` | Sequence-level prompt layers |
| `action_prompt` | **Multi-clip only (2+ videos).** Shared consistency across all in-betweens — not narrative. Empty for single-video sequences. Examples: `2 beats per second dance`, `driving 10 foot per second dolly shot`. |

Single-video sequence: put all motion in `video.inbetween_prompt` only (e.g. *holds the product up to camera and smiles*).

### video_plan (all four combinations)

**One keyframe:**

| open_start | open_end | Videos | Anchors |
|:--:|:--:|--:|---|
| T | F | 1 | open → keyframe |
| F | T | 1 | keyframe → open (app default) |
| T | T | 2 | open → keyframe → open |
| F | F | 0 | invalid — use **2 keyframes** (`layout_end`) |

**Both false + 2 keyframes:** one true first-frame/last-frame video (keyframe → keyframe). Set `ShotSpec.layout_end`.

**Both true + 1 keyframe:** two in-betweens; use `inbetween_prompt` (in) and `inbetween_prompt_out` (out).

Builder: `recommend_video_plan()` (T/F or F/T only), `describe_video_plan(open_start, open_end, keyframe_count)`.

### Video chain logic (multi-keyframe oner)

**End-frame support is a video-workflow capability, not a given.** A oner only plays as one continuous take if the chosen **video workflow** supports **end-frame conditioning** (interpolating start keyframe → end keyframe). **Image model family is irrelevant here** — it governs only how a keyframe *image* is created; once a keyframe exists it is just a PNG the video workflow consumes identically. End-frame support is decided **solely by the video workflow**; some are **start-frame only**. Run a oner through a start-only workflow and each in-between can't target the next keyframe, so the sequence renders as a series of **cuts** rather than a continuous morph. This is **not** automatically a failure — a single sequence that reads as cuts can be exactly what the user wants. So, before building a oner: know whether the video workflow does end-frames, and if it doesn't, **tell the user the sequence will read as cuts and let them choose — warn, don't block** (and don't "fix" cuts that were intended). Everything below assumes an end-frame-capable workflow.

With `keyframe_order = [id1, id2, id3]`, `open_start: false`, `open_end: true`:

| Video | start_keyframe_id | end_keyframe_id |
|-------|-------------------|-----------------|
| vid0 | id1 | id2 |
| vid1 | id2 | id3 |
| vid2 | id3 | null |

**Keyframe regen cascade:** Chained oners are `id → vid → id → vid → id`. Regenerating **id3** invalidates **both** adjacent videos — **vid1** (ends at id3) and **vid2** (starts at id3). Same rule at any interior keyframe: clear `selected_video_path` on every video where that keyframe is start or end. Bracketing keyframes stay selected unless the user flagged them. If the user gave no notes on an invalidated clip, regen it with the **same** `inbetween_prompt`; only the anchor still changed. See [generation-guide.md](generation-guide.md) § Keyframe regen invalidates bracketing videos.

Video count = gaps from `_compute_required_gaps` (see `src/editor_helpers.py`).

### Video-count templates (2–3 videos per sequence)

One video per gap. Count gaps **before** storyboarding. When the user asks for *at least 3 videos*, pick a shape that actually yields 3 gaps — **2 keyframes + open end alone is only 2 gaps**.

| Shape | Keyframes | video_plan | Videos |
|:-----:|:---------:|:-----------|:------:|
| A | 1 (middle) | open start + open end | 2 |
| B | 2 | land + open end (`F`/`T` on first KF path) | 2 |
| C | 2 | open start + turn (`T`/`F` landing) | 2 |
| D | 2 | open start + open end | 3 |
| E | 3 | open end only (`F`/`T` chain) | 3 |
| F | 4 | closed (no open) | 3 |

**Rule:** `_compute_required_gaps(open_start, open_end, keyframe_count)` is authoritative. Max practical gap count per sequence is typically 3 for chained oners — do not under-build when user specifies video count.

**All of those videos live in the same sequence** — export/stitch treats them as one continuous chain (oner). For **discrete cuts**, use **separate sequences** (one shot per seq), not more keyframes in one seq.

### Shots vs oner

| User wants | Structure | Builder |
|------------|-----------|---------|
| Multiple shots / cuts / scenes | N sequences (typically 1 KF + 1 video each) | `build_shots()` |
| One continuous take / oner | 1 sequence, N keyframes + chained videos | `build_narrative_sequence()` |

Single-shot sequence default when unspecified: `open_start: false`, `open_end: true` (keyframe → open).

### Approach-and-arrive pattern (not global default)

For beats where motion **approaches and lands on one keyframe** (walk to shelf, reach and pick, enter and settle), **`open_start: true`, `open_end: false`** is often the right shape: one video, open → landing still.

| Pattern | open_start | open_end | When |
|---------|:--:|:--:|------|
| App default (single KF) | F | T | keyframe starts clip |
| **Approach-and-arrive** | T | F | action lands on end keyframe |
| Middle beat + exit | T | T | two videos; higher QC surface |

Do **not** treat approach-and-arrive as the default for every shot — pick `video_plan` from the beat. When changing plan, refresh the video chain (`_refresh_video_chain` / builder) so orphan videos are not left behind.

Profile continuity when landing KF is side-on: lock orientation in `inbetween_prompt` — [prompt-writing-guide.md](prompt-writing-guide.md) §10.

---

## Keyframe

| Field | Purpose |
|-------|---------|
| `layout` | **Primary still prompt** — composition, framing, **explicit frozen anatomy** (not pose shorthand like `mid-stride`). **Anchor maximum-fidelity moments here** (close-ups, product-to-camera, face, hands, readable detail). Do not rely on in-between video to push into uncached detail. |
| `characters` | `[leftCharId, rightCharId]` max 2 |
| `workflow_json` | Comfy workflow file (`pose_1CHAR.json`, etc.) |
| `pose` | Pose PNG path (Default family) |
| `reference_bindings` | Custom family multi-ref slots — see shape below |
| `selected_image_path` | Chosen generated still |
| `negatives.left/right/heal` | Per-side negatives (2-char) |

**`reference_bindings` shape:** keyed by ComfyUI node-id strings as they appear in the active workflow graph (e.g. `"198"`, `"213"`) — not semantic names. Each value:

```json
{
  "semantic": "character | location | style | unset",
  "character_id": "uuid (when semantic is character, or 'source': 'sequence' for setting/style)",
  "reference_image": "gallery pin path"
}
```

> **Confirmed (klein_multi_image.json, 1-1-agent project, 2026-06-27):** node-id keys carry **no semantic label in the workflow itself** — every `LoadImage` reference node in this workflow is generically titled `THM-ImageReference` (`class_type: LoadImage`) regardless of what it ends up bound to. Semantic meaning (`character` / `location` / `style`) comes entirely from how `reference_bindings` populates each slot, not from the workflow graph. `klein_multi_image.json` has **4** reference-load node-ids: `198`, `213`, `218`, `224` — in this project only 3 are bound (`198`→location/setting, `213` and `218`→two characters), leaving `224` free for a third character/style ref. Node-id numbers are still **workflow-specific** — re-derive them per workflow file by grepping its API-export JSON for `class_type: LoadImage` rather than assuming `198`/`213`/`218`/`224` apply to other Custom workflows. Confirms the original TODO's caution: always read `reference_bindings` off a live `load_project()` call against the target project rather than assuming IDs are universal.

Full prompt craft: [prompt-writing-guide.md](prompt-writing-guide.md)

Default family workflows: 0 chars → `pose_OPEN.json`, 1 → `pose_1CHAR.json`, 2 → `pose_2CHAR.json`.

---

## Video

| Field | Purpose |
|-------|---------|
| `start_keyframe_id` | Start still (`null` = open start) |
| `end_keyframe_id` | End still (`null` = open end) |
| `inbetween_prompt` | **Motion prompt** between anchors |
| `negative_prompt` | Video-specific negatives |
| `duration_override_sec` | Clip length override — **whole seconds only, 1–10** (app UI radio) |
| `selected_video_path` | Chosen output MP4 |

### Model-aware duration planning

Two layers — plan storyboards against **both**:

| Layer | Source | Role |
|-------|--------|------|
| THM UI cap | `DUR_CHOICES` 1–10s (`clip_duration_choices()`) | What the app allows per clip |
| Model recommendation | `video_model_family` + `video_workflow_json` | Default family → `i2v_base.json`; Custom → stored workflow |

| Workflow / model | Recommended | Max tolerable | Notes |
|------------------|-------------|---------------|-------|
| Wan 2.2 (`video_model_family: default`, `i2v_base.json`) | 5s | 8s | Weird results beyond 8s |
| LTX / BYO (`video_model_family: custom`) | 5s | 10s (THM UI cap) | Use Custom family; pick workflow in Generation Defaults |

Derive active workflow from project JSON. Sum `duration_override_sec` values against model recommendation — not a flat 10s per clip or fixed total runtime (e.g. 120s).

### Frame-hold clauses (chained oners)

When subjects appear in both start and end keyframes, append hold language to `inbetween_prompt` so mid-clip exits do not fight the anchors. See [prompt-writing-guide.md](prompt-writing-guide.md) §5 (Frame-hold continuity).

| Gap type | Template (adapt subject noun) |
|----------|-------------------------------|
| Closed KF→KF | `Every {subject} present in BOTH the start and end keyframe stays fully visible inside the frame for the entire clip — neither exits off-screen.` |
| Open end (`end_keyframe_id: null`) | `Every {subject} in the start keyframe stays fully visible for the first two thirds of the clip; only in the final third may they exit off-screen.` |
| Open start (`start_keyframe_id: null`) | `{Subject} may enter from off-screen since absent from the start keyframe, but once visible must remain fully inside the frame until the clip ends.` |

Pair closed holds with negatives: `subject exiting frame, subject walks off screen, empty frame without subjects`.

---

## Prompt template placeholders (assembled at generation)

**Keyframe (image):**

```
[keyframe.layout]
[sequence.setting_asset]
[sequence.setting_prompt]
[project.style_prompt]
[char.prompt]
[sequence.style_asset]
[sequence.style_prompt]
```

**Video (inbetween):**

```
[sequence.action_prompt]
[video.inbetween_prompt]
[sequence.setting_asset]
[sequence.setting_prompt]
[sequence.style_asset]
[sequence.style_prompt]
[project.style_prompt]
```

Videos do **not** include character/layout directly — anchored by start/end keyframes.

---

## Agent: do not overwrite on re-edit

Unless the user explicitly asks:

| Entity | Protected fields |
|--------|------------------|
| Keyframe | `selected_image_path`, `pose`, `reference_bindings`, `controlnet_settings`, `sampler_seed_start` |
| Video | `selected_video_path` |
| Asset | `reference_image` |

Always reload from disk with `load_project()` before editing.

---

## Builder API (sidecar)

Location: [thm-agent/builder.py](../../../thm-agent/builder.py)

| Function | Role |
|----------|------|
| `create_blank(name, family=…)` | New project with defaults |
| `clone_project_from_host(host_path, new_name, dest_path=…)` | New JSON: host globals + blank sequences; host read-only |
| `add_character/setting/style(data, name, prompt, *, negative="", lora_keyword="", generator_prompt="", generator_negative="")` | Append asset library entries — keyword-only after `prompt`; note **`negative=`**, not `negative_prompt=` |
| `build_shots(data, shots=[ShotSpec(…)…])` | **Multiple cuts** — one sequence per shot |
| `build_shot(data, shot=ShotSpec(…))` | Single discrete cut (one sequence) |
| `build_narrative_sequence(data, beats=[BeatSpec(…)…])` | **One oner** — chained keyframes in one sequence |
| `recommend_edit_structure(brief)` | `"cuts"` vs `"oner"` heuristic |
| `normalize_clip_duration_sec(n)` / `clip_duration_choices()` | Whole-second clip length (1–10) |
| `recommend_video_plan(layout, inbetween)` / `resolve_video_plan(...)` | `open_start` / `open_end` for single-video clips |
| `describe_video_plan(open_start, open_end, keyframe_count)` | Human-readable gap summary |
| `ShotSpec.layout_end` | Second keyframe for both-closed KF→KF shot |
| `ShotSpec.inbetween_prompt_out` | Second video when both-open, one keyframe |
| `ShotSpec` — other optional fields | `open_start`, `open_end` (explicit plan override), `duration_sec_out` (second video's length when both-open), `setting_prompt`, `style_prompt`, `action_prompt` (per-shot overrides of the sequence-level fields of the same name) — not all enumerated above; read the dataclass before relying on a field not in this table |
| `remove_placeholder_sequences(data)` | Drop empty seq from `create_blank` before building shots |
| `patch_field(data, dot_path, value)` | Surgical field update — **returns a new dict; reassign (`data = patch_field(...)`) before `save_project`. It does not mutate in place.** |
| `preserve_generation_fields(old, new)` | Round-trip merge |
| `validate_project(data)` | List issues |
| `save_project(path, data)` | Validate + atomic write |
