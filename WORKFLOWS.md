# THM Workflow Compatibility Guide

THM drives ComfyUI workflows by finding controllable nodes in the workflow JSON. Node IDs are irrelevant; THM looks at node titles (`_meta.title`) and, for legacy workflows, some node classes.

The preferred convention is to prefix controllable node titles with `THM-`. Existing legacy titles are still supported as fallbacks so current workflows keep working.

If a node is not tagged or named with a recognized legacy title, THM leaves it alone and the workflow uses whatever value is baked into the template. That may be exactly what you want.

If a node is controlled by THM, THM owns that input fully. Blank values in THM are written as blanks; baked-in workflow values are not retained as defaults for controlled inputs.

This guide describes the image- and video-generation contracts. Image and video runners share the same tag resolution rules (`THM-*` first, legacy titles second).

---

# THM Tags And Legacy Fallbacks

THM first looks for a `THM-*` tag. If one is present for a control, it uses the tagged nodes and does not also update legacy fallback nodes for that same control. If no matching tag is present, THM falls back to the legacy titles/classes listed below.

Unknown `THM-*` tags are ignored safely for now.

**Tag matching is case-insensitive** (`thm-prompt`, `THM-SEED`, and `Thm-Lora` are equivalent). Canonical casing in this guide is recommended for readability.

---

## Checkpoint

| Preferred tag | Legacy titles | Class | What gets injected |
|---|---|---|
| `THM-Checkpoint` | `MainCheckpoint`, `LeftCheckpoint`, `RightCheckpoint`, `Load Checkpoint` | `CheckpointLoaderSimple`, `UNETLoader`, … | `ckpt_name` or `unet_name` ← project model |

If a checkpoint control is tagged, THM **always** overwrites it with the project/look model. Generation **errors** if a tagged checkpoint exists but no model is specified.

If absent (no tag and no legacy title), the checkpoint baked into the workflow template is used as-is.

---

## LoRA marker

| Preferred tag | Legacy titles | Class | What happens |
|---|---|---|
| `THM-Lora` | `MainLora`, `LeftLora`, `RightLora` | **Any** (e.g. `ModelPassThrough`, rgthree, `LoraLoader`, `LoraLoaderModelOnly`) | Marker only. **rgthree** / `ModelPassThrough`: THM clears baked rgthree slots, sets **Bypass** (inline), injects after the marker (`checkpoint → THM-Lora → Injected_…`). **Native `LoraLoader` / `LoraLoaderModelOnly`**: marker is **muted (Never)** and inject attaches upstream (empty `lora_name` fails Comfy `/prompt` validation even when bypassed). Downstream is rewired to the inject chain end. |
| `THM-LoraAfterThisNode` | _(none)_ | **Any** | Video-only alias for single-path injection. Same mechanics as `THM-Lora`: LoRA nodes inserted **downstream** of the tagged marker; consumers rewired to chain end. Prefer on new video exports. |

LoRAs are not configured directly in the project. They are embedded in prompt text with `__lora:…__` tags (character prompts, style prompts, video prompts, etc.). Tags are stripped before text reaches `CLIPTextEncode`; remaining words are still trigger/description text.

### LoRA tag syntax (shared by image and video)

| Tag in prompt | Meaning |
|---|---|
| `__lora:mylora.safetensors:0.75__` | First strength `0.75`; second lane uses the same value |
| `__lora:mylora.safetensors:0.75:0.5__` | First strength `0.75`; second strength `0.5` |

How the two strengths are applied:

| Pipeline | First value | Second value (if present) |
|---|---|---|
| **Image** (`inject_loras`, when `clip_support`) | `strength_model` on `LoraLoader` | `strength_clip` on `LoraLoader` |
| **Image** (model-only graph, Klein / Z / UNET) | `strength_model` on `LoraLoaderModelOnly` | Ignored |
| **Video** (legacy WAN 14B dual pass, `inject_video_dual_loras`) | High-noise pass (`THM-Lora-High` / `HighNoiseUnet` chain) | Low-noise pass (`THM-Lora-Low` / `LowNoiseUnet` chain) |
| **Video** (new single-path, `THM-LoraAfterThisNode`) | `strength_model` on injected `LoraLoader` / `LoraLoaderModelOnly` chain | Second tag value ignored (model-only chain) |

LoRA **filenames** on video still come from `lora_pairs.csv` / `resolve_lora_pair` (high vs low **files**); only **strengths** come from the tag.

### Auto-detect: model-only vs model + CLIP

THM inspects the graph (not the model family):

| Detection | Injection | CLIP handling |
|---|---|---|
| Marker has a `clip` input **or** a `CLIPTextEncode` reads `clip` from the marker | Chain of **`LoraLoader`** nodes from `[marker, 0]` / `[marker, 1]` | Downstream `clip` links are rewired to the end of the chain |
| Marker is model-only (typical Klein / Z / UNET + separate `CLIPLoader`) | Chain of **`LoraLoaderModelOnly`** nodes from `[marker, 0]` | Encoders that pointed at the marker for `clip` are rewired to `[marker, 1]` passthrough or upstream CLIP |

Logs include `[LORA] clip_support=true|false base=… marker=…` per marker group.

`scan_workflow_controls()` sets `lora_clip_support` when any marker needs dual-path injection.

To bake LoRAs into a workflow permanently, use a normal LoRA loader and **do not** tag it.

---

## Positive Prompt

| Preferred tag | Legacy titles | Class | What gets injected |
|---|---|---|
| `THM-Prompt` | `MainPrompt`, `LeftPrompt`, `RightPrompt`, `HealPosPrompt`, `PosPrompt` | `CLIPTextEncode` | `text` ← assembled prompt |

The prompt is assembled from the project's prompt template, combining keyframe layout, setting, style, character keyword, and character description.

---

## Negative Prompt

| Preferred tag | Legacy titles | Class | What gets injected |
|---|---|---|
| `THM-NegativePrompt` | `MainNegPrompt`, `NegPrompt`, `LeftNegPrompt` | `CLIPTextEncode` | `text` ← merged negative prompt |

Merged from `project.negatives.global` + `project.negatives.keyframes_all` + **assigned character** `project.characters[].negative_prompt` (Assets tab) + keyframe Properties negative (`negatives.left` / `right` / `heal`). Single-pass / `THM-NegativePrompt` workflows use the one active character; 2CHAR workflows map character 1 → `LeftNegPrompt`, character 2 → `RightNegPrompt` (heal pass uses global + `heal_all` + keyframe heal only). If the merged value is blank, THM writes a blank prompt so workflow-baked negative text is cleared.


---

## Pose / Reference Image

| Preferred tag | Legacy titles | Class | What gets injected |
|---|---|---|
| Preferred tag | Legacy titles | Class | What gets injected |
|---|---|---|
| `THM-ImageReference` | _(none)_ | `LoadImage` | **Custom family:** up to four duplicate bare tags; semantics via `reference_bindings` keyed by Comfy **node id**. **Default family pose:** use legacy `MainImageAndMask` (not a THM tag). |

Deprecated THM reference tags (`THM-PoseReference`, `THM-CharacterReference`, `THM-LocationReference`, `THM-SettingReference`, `THM-StyleReference`) are no longer recognized — use duplicate bare `THM-ImageReference` only.

### Tagged vs untagged `LoadImage` nodes

Before each run, THM **clears** every tagged pose/character/location/style reference node (empty `image` input). Example filenames baked into workflow JSON from tests are not kept.

**Custom image model family:** if a slot has no image for this keyframe, THM **mutes** that branch and **rewires every parallel `ReferenceLatent` stack** (positive and negative) to skip it, including repointing `KSampler` when trailing slots are empty. The **Workflow capabilities** accordion shows discovered `image1` / `image2` order from graph wiring. THM prepends a **reference prelude** to the positive prompt (`[thm.reference_prelude]` in the project prompt template): generic lines per active slot (e.g. `image1 is a character reference.`) — not asset names. Reference those indices in `[keyframe.layout]` (e.g. “image2 stands left”).

**Custom reference slots UI:** when the workflow exposes bare `THM-ImageReference` loaders and the project uses **Custom** image model family, the keyframe **Properties** panel shows a **Reference Images** accordion (collapsed by default) with up to four slots in a **2×2 grid** (instead of character dropdowns). Each slot has a semantic dropdown (`—`, **Poses**, any project **character**, **location**, or **style** name) and a gallery (poses library or the asset’s `_characters` / `_locations` / `_styles` folder). At most one slot may select **Poses** per keyframe. The fat **Current Pose** accordion (library upload, ControlNet sliders) is **hidden** in Custom family — pose is chosen via the slot semantic **Poses** only. Default family keeps the pose section unchanged.

Bindings live on the keyframe as `reference_bindings` keyed by Comfy **node id** (not ordinal `1`–`4` or tag suffix). Legacy ordinal keys (`"1"`, `""`, semantic keys) are migrated once on load or workflow change by wiring-order position. Bindings mirror legacy `pose` / `characters[]` for run time. Each binding may include:

| Field | Meaning |
|---|---|
| `semantic` | `pose`, `character`, `location`, `style`, or `unset` |
| `source` | `"sequence"` — use sequence `setting_id` / `style_id` until the user picks another asset in the dropdown |
| `setting_id` / `style_id` / `character_id` | Explicit asset override (clears sequence-only default for that slot) |
| `reference_image` | Optional **keyframe pin** (gallery pick); does not change the Assets tab default |

**Sequence auto-assign:** when a sequence has `setting_id` and/or `style_id`, THM assigns the first **unbound** generic slot (by wiring order) to location, then the next free slot to style (`source: "sequence"`). Explicit `{node_id: {semantic: unset}}` entries are not overwritten.

**Pin reset:** changing the semantic dropdown to a **different** asset clears `reference_image`; re-selecting the original asset after visiting another choice clears the pin and re-reads the live Assets default. Re-clicking the same dropdown value without an intermediate change does not clear the pin.

**Sequence location/style image pickers:** on the **Sequence** Properties panel, each Location and Style dropdown has a reference gallery (same layout as Assets). Optional sequence pins: `setting_reference_image`, `style_reference_image` (gallery pick only — does not change Assets tab defaults). Effective image order:

1. Keyframe binding `reference_image` pin (if set)
2. Sequence pin (`setting_reference_image` / `style_reference_image`)
3. Asset canonical `reference_image` from the Assets tab
4. First image in that asset’s `_locations/<id>/` or `_styles/<id>/` gallery folder

Keyframes with `source: "sequence"` inherit the sequence effective image unless they have their own binding pin. Changing the sequence Location/Style dropdown to a **different** asset clears that sequence pin (same different-then-same rule as keyframe slots).

**Default** family keeps the existing pose section and character dropdowns unchanged.

**Custom multi-ref tagging:** use up to four **duplicate bare** `THM-ImageReference` titles on `LoadImage` nodes wired into a linear `ReferenceLatent` stack (see `workflows/pixa-four-image_vague.json`). Do not use `-2` / `-3` suffixes or mixed legacy ref tags in Custom workflows. Saved bindings use Comfy node ids; when workflow JSON changes, bindings remap by slot wiring order.

**Pose flip (keyframe Properties, Default family):** **Flip Horizontal** / **Flip Vertical** insert `ImageFlip+` nodes after the pose reference output on `THM-PoseReference` and legacy `MainImageAndMask` workflows.

**Prompt reference preview:** above the keyframe **Prompt** field, THM shows one thumbnail per active reference labeled **`image1`**, **`image2`**, …, a read-only **Reference prelude** text box (same lines as generation), then the layout **Prompt**. Updates when bindings, workflow, or node selection change.

**Default family:** inactive tagged loaders stay cleared; pose ControlNet workflows are unchanged.

To keep a fixed image inside the graph, leave the node **untagged**.

### Image model family (project setting)

On the **Project** tab → **Generation Defaults**:

| UI | JSON | Behavior |
|----|------|----------|
| **Default** | `project.image_model_family`: `"default"` | No workflow dropdown on the Project tab; resolution always uses `pose_OPEN.json` (editor pose pick still swaps `pose_1CHAR` / `pose_2CHAR` per keyframe). **Keyframe Model Configuration** (model, steps, CFG, sampler, scheduler) is visible and drives every run via `set_generation_settings`. **Keyframe** / **Heal Pass** negatives on the Project tab follow a capability scan of `pose_OPEN.json` (heal only when the workflow exposes `HealNegPrompt`). Global and In-between negatives stay visible. |
| **Custom** | `"custom"` | **Default workflow** dropdown (`project.default_workflow_json`, initial `pose_OPEN.json`) for new keyframes and asset tests; pose pick does **not** change workflow. **Keyframe Model Configuration** is hidden. **KSampler** steps/CFG/sampler/scheduler always come from the workflow JSON (including when the selected file is `pose_OPEN.json`); only **seed** is project-driven. **Heal Pass Negative** is always hidden; **Keyframe Negative** is shown only when the selected workflow scan includes a negative-prompt slot. Global and In-between negatives stay visible (video). Reference prelude + branch skip for empty slots (see above). |

`project.default_workflow_json` is stored for Custom mode. In Default mode it is normalized to `pose_OPEN.json` and ignored for path resolution (`effective_default_workflow_filename`). **Switching** the project family from **Custom** to **Default** (Project tab radio) rewrites every keyframe `workflow_json` to `pose_OPEN.json` / `pose_1CHAR.json` / `pose_2CHAR.json` from each keyframe’s pose (cleared pose → `pose_OPEN.json`). Opening or reloading a Default-family project does **not** rewrite per-keyframe workflows; the editor only auto-picks workflow when the user changes pose on a keyframe.

**Pose library generation** (Assets pose tab, editor generate-pose) uses **hardcoded** cfg/steps/sampler/scheduler (Expressive / Fast modes) regardless of image model family; temp runs force Default-family sampler injection so those values apply even when the parent project is Custom.

**Assets character test:** the pose picker is hidden temporarily; tests run without a pose reference. **Assets workflow dropdown:** hidden in Default family (fixed `pose_OPEN.json` for location/style/character tests); visible in Custom with session override defaulting to `project.default_workflow_json`. **Assets Generate** merges each asset's user **Negative Prompt** with per-type `TEST_*_DEFAULT_NEGATIVE` constants in `single_gen_helpers.py` at test time (not saved to project JSON), user text first — same pattern as positive test anchors.

Future: keyframe editor UI may be driven from the project default workflow regardless of per-keyframe `workflow_json`.

### Video model family (project setting)

On the **Project** tab → **Generation Defaults** (Video column):

| UI | JSON | Behavior |
|----|------|----------|
| **Default** | `project.video_model_family`: `"default"` | **Default video workflow** dropdown hidden. All in-betweens use [`i2v_base.json`](workflows/i2v_base.json) (legacy Wan express path). Steps/FPS visibility follows capability scan of that workflow. |
| **Custom** | `"custom"` | **Default video workflow** dropdown visible. Stored path in `project.inbetween_generation.video_workflow_json` drives runs and capability scans (LTX, fun_inpaint, THM-tagged exports, etc.). |

`video_workflow_json` is still stored in Custom mode. In Default mode it is normalized to `i2v_base.json` on family switch (`migrate_video_to_default_workflow`). Projects without `video_model_family` infer **Custom** when the stored workflow basename is not `i2v_base.json`.

**Image and video families are independent** — e.g. Default image + Custom video (LTX) is valid.

### Project folders (user-facing)

| Label | JSON list | Folder under project output |
|-------|-----------|----------------------------|
| Character | `project.characters[]` | `_characters/` |
| Location | `project.settings[]` | `_locations/` |
| Style | `project.styles[]` | `_styles/` |

Each asset may have `reference_image` (absolute path) — the **active** reference used at run time. On **Assets** (Characters, Locations, Styles), each asset has a **Reference library** (gallery under `_characters/<id>/`, `_locations/<id>/`, or `_styles/<id>/`):

- **Generate** — test image (session output; not stored until saved).
- **Save to library** — copies the test result into that asset’s folder (versioned filename); does **not** change the active reference.
- **Upload image** — adds a file to the library immediately (no pose-style post-processing).
- **Gallery click** — promotes that file to **Selected reference** (`reference_image` in project JSON).
- **Delete image** — removes a library file; clears `reference_image` if it pointed at that file.

The gallery highlight may reset on refresh; the true selection is always `reference_image` on the asset record. Existing `reference.png` / `reference_2.png` files in asset folders appear in the library and remain valid paths.

**Character → keyframe → generate (single primary slot):**

1. In **Assets → Characters**, save a **reference image** on the character (stored under `_characters/<character-id>/`).
2. On the keyframe, select that character in **Character (main/left)** (`keyframe.characters[0]` = character id).
3. Use a workflow with `THM-CharacterReference` (e.g. `klein_multi_image.json`). At run time, if the character has `reference_image` on disk, the runner injects that path into the unsuffixed `THM-CharacterReference` node (and into `THM-CharacterReference-1` when present as an alias for the same primary character).

Character **prompt text and LoRA tags** are unchanged — they still flow through the existing prompt assembly. There is no per-keyframe reference override; only the Assets `reference_image` for the selected character is used.

`THM-CharacterReference-2` and higher suffixes map to `characters[1]` when a second character is selected; workflows with only one character pick leave those nodes empty after the pre-run clear.

Multi-ref Klein workflows are softer than ControlNet — align prompt verbs with pose/reference content.

**BYO workflow authoring (Custom):** tag optional `LoadImage` nodes, wire a **linear** `ReferenceLatent` stack toward `THM-Prompt`, confirm order in the keyframe editor **Workflow capabilities** accordion. One workflow file can expose all slots; THM activates only what the user picked for each keyframe.

Slot suffixes (`THM-LocationReference-background`, etc.) map to future location slots when multi-location workflows are needed.

---

## Save Image

| Preferred tag | Legacy title | Class | What gets written |
|---|---|---|
| `THM-SaveImage` | `Save Image` | `SaveImage` | `filename_prefix` ← output path scoped to project/sequence/keyframe |

This control must be present for THM to locate the generated output file after generation completes.

---


# Tagged Or Legacy Class-Based Controls

Sampler-like and dimension controls are now tag-first. If a workflow exposes `THM-*` tags for these values, THM updates only the tagged nodes for that control. If no tag is present, THM uses the legacy class-based behavior.

## Sampler

| Preferred tag | Legacy fallback | Inputs written | Source |
|---|---|---|
| `THM-Seed` | `KSampler`, `RandomNoise` | `seed`, `noise_seed`, or `value` | computed per iteration from project seed settings (both families) |
| `THM-Steps` | `KSampler`, `Flux2Scheduler` | `steps` or `value` | **Default** family: `project.keyframe_generation.steps`. **Custom** family: value baked in workflow (not overwritten). |
| `THM-CFG` | `KSampler`, `CFGGuider` | `cfg` or `value` | **Default**: `project.keyframe_generation.cfg`. **Custom**: workflow JSON. |
| `THM-Sampler` | `KSampler`, `KSamplerSelect` | `sampler_name` or `value` | **Default**: `project.keyframe_generation.sampler_name`. **Custom**: workflow JSON. |
| `THM-Scheduler` | `KSampler` | `scheduler` or `value` | **Default**: `project.keyframe_generation.scheduler`. **Custom**: workflow JSON. |
| `THM-KSampler` | — (no class fallback) | `steps`, `cfg`, `sampler_name`, `scheduler` on the tagged node only | **Default** family: same sources as the per-field tags above, for workflows with multiple `KSampler` nodes. Optional suffix (`THM-KSampler-heal`) is for discovery only. |

In legacy workflows (no generation tags), all matching fallback class nodes receive the same values.

### Multi-KSampler (Default family)

When a workflow has more than one `KSampler` (e.g. left/right passes plus a heal pass with different `denoise`), retitle the sampler(s) that should follow **Keyframe Model Configuration** to **`THM-KSampler`**. Leave other samplers with a plain title (e.g. `KSampler`); their steps/cfg/sampler/scheduler stay as baked in the workflow JSON.

- If **any** sampler-parameter tag exists (`THM-Steps`, `THM-CFG`, `THM-Sampler`, `THM-Scheduler`, or `THM-KSampler`), THM does **not** legacy-overwrite untagged nodes for steps/cfg/sampler/scheduler. (`THM-Seed` alone does not enable selective sampler mode.)
- **Seed** still updates every `KSampler` (and `RandomNoise`) unless `THM-Seed` tags exist, so passes stay aligned.
- **`denoise`** is not project-driven; bake it on the non-driven sampler in ComfyUI.

---

## Image Dimensions

| Preferred tag | Legacy fallback | Inputs written | Source |
|---|---|---|
| `THM-ImageSize` | dimension classes | `width`, `height` | project `width` x `height` |
| `THM-Width` | `Width`, dimension classes | `value` or `width` | project `width` |
| `THM-Height` | `Height`, dimension classes | `value` or `height` | project `height` |

Legacy dimension fallback still updates known dimension classes such as `EmptyLatentImage`, `ImageScale`, `ImageCrop`, `Image Blank`, and `EmptyFlux2LatentImage`.

---

## What THM Does Not Touch

- VAE nodes
- CLIP loader nodes
- Node connections and wiring
- Any node whose title/tag is not in the list above
- Any input field not explicitly listed above

---

## Checklist: Adding a New Workflow

- [ ] Save node titled `THM-SaveImage` or legacy `Save Image`
- [ ] Prompt node titled `THM-Prompt` if THM should write the positive prompt
- [ ] Negative prompt node titled `THM-NegativePrompt` if THM should write negatives
- [ ] Checkpoint node titled `THM-Checkpoint` if THM should control the model
- [ ] LoRA loader titled `THM-Lora` if THM should inject LoRA chains
- [ ] Pose/reference `LoadImage` node titled `THM-PoseReference` or `THM-ImageReference` if THM should inject a pose/reference image
- [ ] Optional sampler-like nodes tagged with `THM-Seed`, `THM-Steps`, `THM-CFG`, `THM-Sampler`, or `THM-Scheduler`
- [ ] Optional dimension nodes tagged with `THM-ImageSize`, `THM-Width`, or `THM-Height`
- [ ] Any untagged nodes are intentionally left at their baked-in workflow values

---

## Capability scan (Phase 3)

When a workflow JSON is selected in the Editor, THM can scan it via [`src/workflow_capabilities.py`](src/workflow_capabilities.py) (backed by [`scripts/workflow_controls.py`](scripts/workflow_controls.py)).

- Discovery uses **THM tags and legacy titles**, plus **legacy class fallbacks** where runtime injection uses them (`KSampler`, `RandomNoise`, `Image Blank`, `Flux2Scheduler`, etc.).
- **Image size (THM drives):** grouped `full` / `partial` / `no` — confirms project width and height are both written when `full`.
- **Generation settings (project controls):** per-field **Confirmed** vs **Not controlled** for seed, steps, cfg, sampler, and scheduler (matches `set_generation_settings` / `set_seed`, not tags alone).
- Also reports tagged controls (prompt, lora, …), `lora_clip_support`, `has_pose_control`, and unknown `THM-*` tags.
- **Workflow vs project:** the scan describes the workflow file only. Project Settings may still list values that the workflow graph does not receive (shown as **Not controlled by project**).

**Debug UI (optional, default off):** enable either:

- `features.show_workflow_capabilities` in workspace settings, or
- `"project": { "debug": { "show_workflow_capabilities": true } }` in the project JSON

Then open **Keyframe → Advanced → Workflow capabilities (debug)** after selecting a workflow. Console logs use the `[CAPABILITIES]` prefix.

### 2-character (`pose_2CHAR`) controls

Workflows such as [`pose_2CHAR.json`](workflows/pose_2CHAR.json) use **role-specific node titles** instead of a single `MainPrompt` / `MainLora`. The image runner ([`scripts/run_images.py`](scripts/run_images.py)) and capability scan discover the same titles.

| Node title | Project / keyframe source | Notes |
|------------|---------------------------|--------|
| `LeftLora`, `RightLora` | LoRA tags in left+right composed prompts | Same injected LoRA chain is wired into **both** markers |
| `LeftPrompt`, `RightPrompt` | Per-character prompts (2-char template) | |
| `HealPosPrompt` | Heal pass positive (both characters) | Third pass in the graph |
| `LeftNegPrompt`, `RightNegPrompt` | `negatives.global` + `keyframes_all` + character `negative_prompt` + per-kf `left` / `right` | |
| `HealNegPrompt` | `negatives.global` + `keyframes_all` + `heal_all` + per-kf `heal` | Project **Heal Pass Negative** + keyframe **Negative (heal)** |

When the workflow is a **true 2CHAR graph** (right or heal prompt slots, paired LoRA markers, and/or right/heal negatives), the capabilities panel shows a **2-character pass** section and summary `2char=…,heal=yes` when heal nodes are present. A lone legacy `LeftPrompt` / `LeftNegPrompt` / `LeftLora` (used on some 1-character workflows such as `image_z_image_turbo.json`) does **not** activate 2CHAR mode.

### Phase 4 — Keyframe editor field visibility

When a keyframe’s **Workflow** dropdown changes (or you select a keyframe), [`keyframe_editor_visibility`](src/workflow_capabilities.py) drives which **Properties** fields are shown:

| Field | Shown when |
|-------|------------|
| Pose block + pose library | `pose_reference` in workflow (`THM-PoseReference` or legacy `MainImageAndMask`) |
| Pose / Shape / Outline CN sliders | Legacy `PoseControl` node only (e.g. [`pose_1CHAR.json`](workflows/pose_1CHAR.json)); hidden for Klein / multi-ref workflows that only wire `THM-PoseReference` without `PoseControl` (e.g. [`klein_multi_image.json`](workflows/klein_multi_image.json)) |
| Character (main/left) | Always |
| Character (secondary/right) | Second character reference slot (`THM-CharacterReference-2` / slot `2`) **or** 2CHAR pipeline (split left/right prompts — not required for multi-ref Klein workflows) |
| Prompt | Prompt control in workflow |
| Advanced accordion | Always |
| Inject LoRA | LoRA markers in workflow |
| Negative (left) | `MainNegPrompt`, `NegPrompt`, `THM-NegativePrompt`, or `LeftNegPrompt` |
| Negative (right/heal) | 2CHAR pipeline active |

Hidden fields keep their saved keyframe JSON values; they are not cleared when the workflow changes.

**Generations → Seed** (feature `show_generation_info`): shown when the scanned workflow has a **confirmed** seed target. **Keyframes** use the keyframe **Workflow** dropdown (`KSampler.seed`, `RandomNoise.noise_seed`, or `THM-Seed`). **In-betweens** use the project **default video workflow** via `scan_video_workflow_file` (`THM-Seed`, legacy `SlowMoPrimer` / triple titles, or `KSamplerAdvanced` with `add_noise: enable`). With `show_workflow_capabilities`, the video **Properties → Advanced → Workflow capabilities** panel scans that same project video workflow (see [Video workflow capabilities (debug)](#video-workflow-capabilities-debug) below).

---

# Video Workflows

Video generation ([`scripts/run_video.py`](scripts/run_video.py)) uses the same tag-first / legacy-fallback resolution as images, centralized in [`scripts/workflow_controls.py`](scripts/workflow_controls.py).

Bundled legacy workflow [`workflows/i2v_base.json`](workflows/i2v_base.json) stays **untagged**; legacy node titles keep it working without edits. New exports from ComfyUI should use `THM-*` tags (see checklist below). A tagged reference copy may live under `samples/workflows_openincomfy/` when available.

**Project FPS:** `project.inbetween_generation.fps` (default **16**) is the single source of truth for frame count, `THM-FrameRate` injection, lossless stitch, and export metadata. Frame count: `round(duration_sec × fps) + 1`.

**Project flags** (Rough Draft / quarter-size / upscale) apply when the workflow exposes compatible hooks (tagged `THM-KSampler` / `THM-SlowMoPrimer`, legacy express sampler triple, `Create Video` + FILM upscaler path, etc.).

## Shared with images

| THM tag | Legacy titles | Injected field |
|---------|---------------|----------------|
| `THM-Prompt` | `PosPrompt`, `MainPrompt`, … | `text` on encode node |
| `THM-NegativePrompt` | `NegPrompt`, `MainNegPrompt`, … | `text` |
| `THM-Seed` | `IterKSampler`, `WanFixedSeed`, `SlowMoPrimer` | `seed` / `noise_seed` — legacy triple: all except `seed_exclude_title` (`WanFixedSeed`); tagged workflows: explicit tags only; untagged multi-pass: `add_noise: enable` pass only |
| `THM-SaveImage` | `Save Image` | `filename_prefix` (temp frame saver for lossless stitch) |
| `THM-Width` / `THM-Height` / `THM-ImageSize` | _(image legacy titles)_ | width/height on scale or generator nodes |

## Video-only controls

| Control | THM tag | Legacy fallback | Writes |
|---------|---------|-----------------|--------|
| Video generator | `THM-VideoGenerator` | `WanFirstLastFrameToVideo` | `width`, `height`, and `length` when not split |
| Frame count | `THM-FrameCount` | — | `value` on wired int/float constant (user wires to generator `length`) |
| Frame rate | `THM-FrameRate` | — | `value` on wired constant (user wires to `THM-SaveVideo.frame_rate`) |
| Save video | `THM-SaveVideo` | `SaveVideo` / `VHS_VideoCombine` class | `filename_prefix` only |
| Start keyframe | `THM-StartFrame` | — | `LoadImage.image` |
| End keyframe | `THM-EndFrame` | — | `LoadImage.image` |

Workflows may export **both** frame loaders wired into `THM-VideoGenerator`. At run time THM configures open vs closed use:

| Clip | Start | End |
|------|-------|-----|
| **SE** (closed) | wired + path set | wired + path set |
| **SO** (open end) | wired + path set | disconnected from generator + `LoadImage.image` cleared |
| **OE** (open start) | disconnected + cleared | wired + path set |

Export both entry points from ComfyUI; THM adjusts the graph per clip — no separate workflow files needed.

### Frame input support vs sequence clip type

**Sequence clip type** (SE / SO / OE) describes which keyframes a video slot connects to in the project. **Workflow frame support** describes what the ComfyUI graph can consume — scan reports `supports_start_frame` / `supports_end_frame`.

| Workflow example | Start supported | End supported |
|------------------|-----------------|---------------|
| LTX i2v (`THM_video_ltx2_i2v.json`) | yes (`THM-StartFrame`) | **no** |
| 5B FLF2V (`THM_video_wan2_2_5B_ti2v_FLF2V.json`) | yes | **no** (generator has `start_image` only) |
| fun_inpaint | yes | yes |
| Legacy `i2v_base.json` | yes (`StartImage` + Wan) | yes (`EndImage` + Wan) |

Detection: `THM-StartFrame` / `THM-EndFrame` tags, `THM-VideoGenerator` wired `start_image` / `end_image` inputs, or legacy Wan + `StartImage` / `EndImage` titles.

**Runner:** `run_video.py` only requires keyframe picks for sides the workflow supports (e.g. LTX SE between two keyframes does **not** require an end pick).

**UI:** In-between inspector shows an empty thumbnail slot when a side is not supported by the workflow (same visual as an open sequence boundary).

**LTX dual-pass steps:** `THM-Steps` drives the tagged `LTXVScheduler` pass; separate `ManualSigmas` nodes stay workflow-baked unless re-wired in ComfyUI.

### LoRA modes (scan picks one)

Pick **one** mode per workflow — do not combine tags.

| Mode | When to use | THM tag(s) | Legacy fallback | Injection |
|------|-------------|------------|-----------------|-----------|
| **Single-path** | One UNet / one model chain | `THM-LoraAfterThisNode` or `THM-Lora` | — | `inject_loras()` — downstream of marker |
| **Dual-pass (high/low)** | Separate high-noise and low-noise chains (WAN 14B style) | `THM-Lora-High` + `THM-Lora-Low` | `HighNoiseUnet`, `LowNoiseUnet` | `inject_video_dual_loras()` + `lora_pairs.csv` high/low file pairing |

**Mutual exclusivity:** never use `THM-LoraAfterThisNode` on the same workflow as `THM-Lora-High` / `THM-Lora-Low`. Dual mode is selected when `THM-Lora-High` is present (`[VIDEO] lora_mode=dual` in logs).

**Dual-pass markers:** tag the UNet anchor per chain (`UNETLoader`) or the last model node downstream samplers consume (`LoraLoaderModelOnly` is fine). Baked workflow LoRAs keep plain titles; prompt `__lora:…__` tags inject **after** each high/low marker.

**Troubleshooting wrong LoRA behavior:**
- Confirm **Default video workflow** in Generation Defaults matches your tagged export (check `debug_workflow_iter*.json` from a run).
- Look for `[VIDEO] lora_mode=dual` vs `single` in the console.
- `[LORA] WARNING: … THM-LoraAfterThisNode has no model input` means single-path mode ran on a `UNETLoader` marker — remove `THM-LoraAfterThisNode` and use high/low tags instead on dual-pass workflows.

### Wired constant pattern (preferred for new workflows)

Tag primitive/int nodes; THM writes their `value`; ComfyUI wiring passes values downstream:

- `THM-FrameRate` → wired to `THM-SaveVideo.frame_rate`
- `THM-FrameCount` → wired to generator `length`
- `THM-Width` / `THM-Height` on separate scale nodes (optional)

THM does **not** rewire the graph — only writes tagged source node values.

### Legacy `i2v_base.json` behavior

When `THM-StartFrame` / `THM-EndFrame` are absent, the runner wires keyframes through `LoadImage` → `StartImage` / `EndImage` scale nodes → `WanFirstLastFrameToVideo`.

**Sampler step tiers** (project **Video steps** applies to tiers 1–3; Generation Defaults hides the field otherwise):

| Tier | Detection | Project `video_steps_default` |
|------|-----------|--------------------------------|
| **Tagged chain** | `THM-KSampler` and/or `THM-SlowMoPrimer` | Yes — pass-range split |
| **Legacy express triple** | Titles `SlowMoPrimer` + `IterKSampler` + `WanFixedSeed` (allowlisted; not THM tags) | Yes — iter/wan split; primer fixed at **2** in code |
| **Tagged scheduler** | `THM-Steps` on a scheduler node (e.g. `LTXVScheduler`) | Yes — writes `steps` on the tagged node |
| **Workflow-baked** | Generic `KSampler` / other samplers without tags or legacy triple | **No** — keep steps baked in the ComfyUI export (e.g. 5B FLF2V @ 25) |

Retag with `THM-KSampler` for Wan-style pass-range control, or `THM-Steps` on the scheduler node for LTX-style workflows.

**Sampler step splitting** supports two injection paths (tagged first, legacy second):

| Path | Detection | Step source |
|------|-----------|-------------|
| **Tagged** | `THM-KSampler` chain and/or `THM-SlowMoPrimer` | `project.inbetween_generation.video_steps_default` (default **14**) |
| **Tagged scheduler** | `THM-Steps` (e.g. on `LTXVScheduler`) | Same — writes `steps` on the tagged node |
| **Legacy triple** | Titles `SlowMoPrimer`, `IterKSampler`, `WanFixedSeed` all present | `project.inbetween_generation.video_steps_default` (default **14**); primer fixed at **2**, iter/wan split proportionally |

Tagged and legacy paths also enable temp frame save + optional FILM upscaler when upscale flags apply.

### Tagged video sampler passes

| THM tag | Role | Injected fields |
|---------|------|-----------------|
| `THM-SlowMoPrimer` | Optional slomo / primer pass | `steps`, `start_at_step`, `end_at_step` — **2 steps** fixed in `run_video.py` (`PRIMER_STEPS`); not exposed in Generation Defaults |
| `THM-KSampler` | One or more chain passes (duplicate bare titles) | Same fields — contiguous ranges after primer |

**Discovery:** duplicate bare `THM-KSampler` titles (like image reference slots). Order follows the latent chain backward from `VAEDecode`, not numeric node id.

**Full mode:** remaining chain budget after primer is split **equally** across ordered `THM-KSampler` nodes (remainder to last pass).

Image-side `THM-KSampler` bundles steps/cfg/sampler/scheduler for keyframes — **different semantics** from video pass-range splitting.

**Project fields** (Generation Defaults):

- **Video steps** → `video_steps_default` (default 14) — shown only when tier 1 or 2 above applies
- **Frame rate** → `fps` — shown only when the workflow has `THM-FrameRate` / `THM-FPS` (injected at run time)
- **Default in-between length** → `duration_default_sec`
- **LoRA normalization** → per-channel toggles and max saturation (image FG/BG, video)

Legacy title `SlowMoPrimer` is still discovered when `THM-SlowMoPrimer` is absent. To change primer step count, edit `PRIMER_STEPS` in `scripts/run_video.py` — workflows may include the tag but the UI does not configure it.

### Video seed injection

| Workflow style | How seed is applied |
|----------------|---------------------|
| **Legacy triple** (`SlowMoPrimer` / `IterKSampler` / `WanFixedSeed`) | Project seed on **all** legacy seed nodes except `seed_exclude_title` (`WanFixedSeed` default) — no THM tags required |
| **`THM-Seed` tagged** | Explicit tagged nodes only (exclude title still honored) |
| **Tagged multi-pass** (duplicate `THM-KSampler`, no legacy titles) | Auto: **`add_noise: enable`** pass only (e.g. fun_inpaint first pass); no `THM-Seed` required |

Per-iteration seed comes from `project.inbetween_generation.seed_start` + `advance_seed_by`. Hidden project fields `seed_target_title` / `seed_exclude_title` remain for compatibility; legacy triple ignores narrow `seed_target_title` filtering.

### Video workflow capabilities (debug)

When **`show_workflow_capabilities`** is enabled (workspace `features.show_workflow_capabilities` or `project.debug.show_workflow_capabilities`), select an **in-between (video) node** in the Editor and open **Properties → Advanced → Workflow capabilities (debug)**.

Unlike the keyframe panel, this scan uses the project **Default video workflow** from Generation Defaults (`project.inbetween_generation.video_workflow_json`), not the keyframe workflow dropdown. It runs [`scan_video_workflow_file`](src/workflow_capabilities.py) + [`format_video_capabilities_markdown`](src/workflow_capabilities.py), aligned with [`discover_video_capabilities`](scripts/workflow_controls.py) and the **`[VIDEO]`** console lines from [`run_video.py`](scripts/run_video.py) (LoRA mode, frame I/O, sampler path, seed targets). It does **not** show image-only sections (reference wiring, 2CHAR, image generation settings, or image “Not in workflow” lists).

**`show_generation_info`** is separate: it controls **Generations → Seed** visibility in the Editor (keyframe seed from the keyframe workflow; in-between seed from the same project default video workflow scan). You can enable one flag without the other.

## BYO video workflow checklist

Minimal tags for a new ComfyUI export:

```
THM-Prompt
THM-NegativePrompt
THM-StartFrame              LoadImage — start keyframe
THM-EndFrame                LoadImage — end keyframe
THM-VideoGenerator          generator — width + height (and length if not split)
THM-FrameCount              int constant → wire to generator length   (optional split layout)
THM-FrameRate               float constant → wire to THM-SaveVideo frame_rate
THM-SaveVideo               VHS_VideoCombine / SaveVideo — filename_prefix only
THM-Width / THM-Height      separate scale nodes (optional)
THM-LoraAfterThisNode       single-path LoRA anchor (one UNet chain only)
THM-Lora-High               dual-pass high-noise LoRA anchor (with THM-Lora-Low)
THM-Lora-Low                dual-pass low-noise LoRA anchor (with THM-Lora-High)
THM-Seed                    optional
THM-SlowMoPrimer            optional KSamplerAdvanced primer pass
THM-KSampler                one or more chain passes (duplicate bare titles)
```

**Do not tag:** CLIP/VAE/checkpoint loaders, baked workflow LoRAs.

Console logs use the `[VIDEO]` prefix when controls are found or missed (helps BYO workflow debugging).
