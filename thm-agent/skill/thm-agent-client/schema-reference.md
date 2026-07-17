# THM project JSON — V2 schema reference

Canonical example: [samples/THM-demo.json](../../../samples/THM-demo.json)

Full prompt rules: [prompt-writing-guide.md](prompt-writing-guide.md) · [docs/prompt_design.md](../../../docs/prompt_design.md)

---

## THM primitives — map the request onto these first

Every brief — any workflow, any model, any genre — decomposes onto five primitives. Do this mapping **before** building JSON; structure mistakes come from skipping it, not from schema errors.

| Primitive | What it is |
|-----------|------------|
| **Keyframe** | An **anchored ground-truth still** — the frame that must be exact (product close-up, face, landing, reveal). If a moment must be pixel-faithful, it must be a keyframe. |
| **Video** | **Motion between/from/to anchors** — never itself ground truth. Whatever a video is asked to invent (an unanchored landing, a missing endpoint) will drift. |
| **Assets** (`characters[]` / `settings[]` / `styles[]`) | **Reusable identity references** — including settings/locations as first-class reviewable plates, not just people/products. Asset generation may run a different model family than keyframes (see [asset-library-guide.md](asset-library-guide.md) § Mixed-model pipelines). |
| **`selected_*_path`** | **Persisted decisions / state** — the on-disk ground truth of what's approved. |
| **Sequence** | A **shot** (discrete cut) or a **chained oner**. |

Decomposition question for any request: *which moments are anchors, what moves between them, which identities recur?* The answers dictate keyframe placement, `video_plan`, and the asset library — in that order.

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
- **`__lora:` tags need the exact on-disk filename *including* `.safetensors`** — *unless* the name follows the `_high_noise`/`_low_noise` pair convention, where the resolver rebuilds both filenames with the extension for you. A standalone LoRA with no noise suffix is passed through verbatim, so an extensionless tag produces an invalid `lora_name` and ComfyUI fails prompt validation. When unsure, write the full filename.
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

**The keyframe fidelity rule (see [Keyframe](#keyframe) `layout`) is a planning input, not a QC check.** Decide first which frame carries the uncachable detail — that frame becomes the keyframe — then read the plan off the table from where it sits. The failure this prevents: a start-only shot asked to "land on X" leaves the landing model-invented. Derive the plan from the request wording: [prompt-writing-guide.md](prompt-writing-guide.md) §5 § Derive the plan from the request wording.

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

> **Partner-API node sets:** for partner workflows (Seedance/ByteDance etc.) end-frame support is a property of the **node set**, not of any THM workflow tag. Some partner nodes take a single image that is always the *first* frame (no end-frame conditioning at all); a first+last-frame node requires **both** frames. An open-start (end-frame-only) beat has no valid mapping on a start-only node set and must be rewired to start-frame with mirrored motion in the prompt. Partner graphs also don't auto-disconnect unused frame loaders, so keep separate i2v and flf2v workflow files and pick per-clip. See [generation-guide.md](generation-guide.md) § Custom / partner video workflows.

With `keyframe_order = [id1, id2, id3]`, `open_start: false`, `open_end: true`:

| Video | start_keyframe_id | end_keyframe_id |
|-------|-------------------|-----------------|
| vid0 | id1 | id2 |
| vid1 | id2 | id3 |
| vid2 | id3 | null |

**Keyframe regen cascade:** Chained oners are `id → vid → id → vid → id`. Regenerating **id3** invalidates **both** adjacent videos — **vid1** (ends at id3) and **vid2** (starts at id3). Same rule at any interior keyframe: clear `selected_video_path` on every video where that keyframe is start or end. Bracketing keyframes stay selected unless the user flagged them. If the user gave no notes on an invalidated clip, regen it with the **same** `inbetween_prompt`; only the anchor still changed. See [generation-guide.md](generation-guide.md) § Keyframe regen invalidates bracketing videos.

Video count = gaps from `_compute_required_gaps` (see `src/editor_helpers.py`).

**Two-anchor controlled interpolation (forcing a hard camera move).** An open-start clip landing on an unusual end pose (e.g. a true top-down) can't be forced there by prompt alone — end-frame conditioning isn't strong enough and the model invents an intermediate view. The reliable fix is to give the clip **two** anchors so it can only interpolate the target→target: add a matching start keyframe, set `keyframe_order = [start, end]`, `video_plan = {open_start: false, open_end: false}`, and set the video's `start_keyframe_id`/`end_keyframe_id`. Two anchors are **necessary but not sufficient** — the anchors must share **every** element. If an element present in one anchor is missing from the other (a prop dropped in a tighter crop), the model cross-fades to reconcile the vanishing element instead of moving the camera, producing a mid-clip dissolve. QC any interpolated clip by comparing the two anchor frames **element-for-element** and scanning sampled frames for anything appearing/disappearing — a mid-clip dissolve is almost always a content mismatch between anchors, not a motion-prompt problem.

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

Mapping request wording ("land on X", "start from X", "travel A→B") to these shapes: [prompt-writing-guide.md](prompt-writing-guide.md) §5 § Derive the plan from the request wording.

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

**Binding-level `reference_image` overrides the sequence setting.** `resolve_location_reference_path_from_binding` checks the binding's own `reference_image` (a "pinned" path) **first**, before the sequence `setting_id`. So you can point one keyframe's location/style/character slot at *any* PNG on disk — including another keyframe's `selected_image_path` — by setting `kf["reference_bindings"][<slot>]["reference_image"]` directly (no copy, no new asset). `normalize_reference_bindings` preserves the field. This is how you make an end keyframe a tight crop of the exact scene in the start keyframe, or bind a purpose-built plate to a single shot.

**`reference_bindings` snapshot the plate path at build time — re-sync after any later re-plate.** The `reference_image` stored in a binding is the path **as it was when the keyframe was built**, not a live pointer to the asset's current `reference_image`. If you re-plate a character/setting (or a group plate) *after* building the timeline, every keyframe built earlier silently keeps the **old** plate path — the Gradio dropdown shows the current plate, masking it; only reading the baked binding path reveals the staleness. Fix: after any re-plate, group re-plate, or re-pin, re-sync every affected keyframe's `reference_bindings[...].reference_image` to the asset's current `reference_image`, and regenerate those keyframes. Build order matters — pin all assets (including group plates) **before** building the timeline, or re-sync afterward. Corollary: after any additive-library reshuffle, verify each pin still exists on disk (`os.path.exists`) — library numbering is per-asset (no `view→gallery_N` convention holds across assets), and a dangling pin surfaces only later as a ComfyUI `LoadImage` 400.

> **Confirmed (klein_multi_image.json, session-confirmed 2026-06-27 / 2026-07-09):** node-id keys carry **no semantic label in the workflow itself** — every `LoadImage` reference node in this workflow is generically titled `THM-ImageReference` (`class_type: LoadImage`) regardless of what it ends up bound to. Semantic meaning (`character` / `location` / `style`) comes entirely from how `reference_bindings` populates each slot, not from the workflow graph. `klein_multi_image.json` has **4** reference-load node-ids in wiring order: `198`, `213`, `218`, `224` (typical roles: `198`→location, `213`/`218`→characters, `224`→free for a third character/style ref). Node-id numbers are still **workflow-specific** — re-derive them per workflow file by grepping its API-export JSON for `class_type: LoadImage` rather than assuming `198`/`213`/`218`/`224` apply to other Custom workflows, and always read `reference_bindings` off a live `load_project()` call rather than assuming IDs are universal. Full procedure for an unfamiliar workflow: [lookup-reference.md](lookup-reference.md) § Introspecting a workflow you've never seen.
>
> **`imageN` numbering renumbers to active slots.** Node-id → *role* is fixed per workflow, but the `imageN` index a slot receives in the prompt is its **position among the *active* (bound, file-exists) references in wiring order** (`assign_effective_image_indices` in `scripts/workflow_controls.py`) — unbound slots are muted and skipped. With all four klein slots bound: `198`→`image1`, `213`→`image2`, `218`→`image3`, `224`→`image4`. With **only** `213` bound, `213` renders as `image1`. The two views coincide only when every lower-wiring-order slot is also bound — write layout `imageN` tags against the *active* set, and confirm empirically via the `[REF] prelude` log lines.

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
| Wan 2.2 (`video_model_family: default`, `i2v_base.json`) | 5s | 8s | Weird results beyond 8s. **Start-only clips have a 3s floor** — a 2s start-only clip can hard-crash the ComfyUI process (no traceback) at the first sampling step; render 3s and trim at assembly. KF→KF (end-frame) clips at 2s are fine. |
| LTX / BYO (`video_model_family: custom`) | 5s | 10s (THM UI cap) | Use Custom family; pick workflow in Generation Defaults |
| Seedance / partner API (`video_model_family: custom`) | — | — | **Minimum duration is 4s** — the partner node rejects `duration_override_sec` below 4 (some tiers clamp 3–12). Clips designed shorter must render at the floor and be trimmed in the edit. |

Derive active workflow from project JSON. Sum `duration_override_sec` values against model recommendation — not a flat 10s per clip or fixed total runtime (e.g. 120s).

**Lab / test sequences must match the target's duration.** When building a test or A/B sequence to tune prompts/LoRAs for *existing* clips, copy `duration_override_sec` from those clips — a new video with no override silently renders at the project's `inbetween_generation.duration_default_sec`, which may be much shorter. A test at half the target duration invalidates the comparison (a beat-count/choreography prompt can't fit, and motion reads as too fast because it's compressed).

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
| `create_blank(name, family=…)` | New project with defaults — **includes one placeholder sequence + keyframe whose `workflow_json` resolves to the default-family workflow (`pose_OPEN.json`) regardless of `family`**; reconcile `default_workflow_json` + placeholder after workflow selection, or let `build_shots` clear it |
| `clone_project_from_host(host_path, new_name, dest_path=…)` | New JSON: host globals + blank sequences; host read-only |
| `add_character/setting/style(data, name, prompt, *, negative="", lora_keyword="", generator_prompt="", generator_negative="")` | Append asset library entries — keyword-only after `prompt`; note **`negative=`**, not `negative_prompt=` |
| `build_shots(data, shots=[ShotSpec(…)…])` | **Multiple cuts** — one sequence per shot |
| `build_shot(data, shot=ShotSpec(…))` | Single discrete cut (one sequence) |
| `build_narrative_sequence(data, beats=[BeatSpec(…)…])` | **One oner** — chained keyframes in one sequence |
| `ShotSpec.character_ids` / `BeatSpec.character_ids` | **Default family only** — populates `keyframe.characters` for pose workflows; has **no effect** on Custom-family `reference_bindings`. Custom projects need an explicit `reference_bindings` pass per keyframe after building — verify bindings are non-empty before trusting any generated image as reference-grounded |
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
