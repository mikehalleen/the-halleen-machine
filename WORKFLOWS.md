# THM Workflow Compatibility Guide


# Title based nodes

These nodes are located by **title** (`_meta.title`), not by node ID. IDs are irrelevant.

Renaming a node is only necessary if you want THM to control that value. If a node is not renamed to a recognized title, THM leaves it alone and the workflow uses whatever value is baked into the template. That may be exactly what you want.

---

## Checkpoint

| Title | Class | What gets injected |
|---|---|---|
| `MainCheckpoint` | `CheckpointLoaderSimple` | `ckpt_name` ← project model |

If absent, the checkpoint baked into the workflow template is used as-is.

---

## LoRA Loader

| Title | Class | What happens |
|---|---|---|
| `MainLora` | `Power Lora Loader (rgthree)` | A chain of `LoraLoaderModelOnly` nodes is inserted between the checkpoint and this node. The node's `model` input is rewired to the end of that chain. |

LoRAs are not configured directly in the project. They are sourced from `__lora:filename:strength__` tags embedded in prompt text (character prompts, style prompts, etc.).

**Important:** If your `MainLora` node has LoRA entries pre-baked into it from when you exported the template, those will run alongside the injected chain — applying the same LoRA twice. Clear any pre-baked entries before saving the template if you want THM to be the sole source of LoRA control.

---

## Positive Prompt

| Title | Class | What gets injected |
|---|---|---|
| `MainPrompt` | `CLIPTextEncode` | `text` ← assembled prompt |

The prompt is assembled from the project's prompt template, combining keyframe layout, setting, style, character keyword, and character description.

---

## Negative Prompt

| Title | Class | What gets injected |
|---|---|---|
| `MainNegPrompt` | `CLIPTextEncode` | `text` ← merged negative prompt |

Merged from `project.negatives.global` + `project.negatives.keyframes_all` + any keyframe-level negative override. Only written if the project defines negative text; otherwise the node is left unchanged.


---

## Pose / Reference Image

| Title | Class | What gets injected |
|---|---|---|
| `MainImageAndMask` | `LoadImage` | `image` ← path to the pose or reference image |

Only written when the keyframe has a pose assigned. If the keyframe has no pose, or the pose is set to `(No pose)`, this node is left unchanged.

---

## Save Image

| Title | Class | What gets written |
|---|---|---|
| `Save Image` | `SaveImage` | `filename_prefix` ← output path scoped to project/sequence/keyframe |

This title must be present for THM to locate the generated output file after generation completes.

---


# Class based nodes

These nodes are located by **class**.

All matching nodes are updated if found, there is no option to not overwrite these.

## Sampler

Targeted by **class**, not title. All nodes of these classes in the workflow are updated.

| Class | Input written | Source |
|---|---|---|
| `KSampler` | `seed` | computed per iteration from project seed settings |
| `KSampler` | `steps` | `project.keyframe_generation.steps` |
| `KSampler` | `cfg` | `project.keyframe_generation.cfg` |
| `KSampler` | `sampler_name` | `project.keyframe_generation.sampler_name` |
| `KSampler` | `scheduler` | `project.keyframe_generation.scheduler` |

If your workflow has more than one `KSampler`, all of them receive the same values.

---

## Image Dimensions

Targeted by **class**, not title. All nodes of these classes in the workflow are updated.

| Class | Inputs written | Source |
|---|---|---|
| `EmptyLatentImage` | `width`, `height` | project `width` × `height` |
| `ImageScale` | `width`, `height` | project `width` × `height` |
| `ImageCrop` | `width`, `height` | project `width` × `height` |
| `Image Blank` | `width`, `height` | project `width` × `height` |

---

## What THM Does Not Touch

- VAE nodes
- CLIP loader nodes
- Node connections and wiring
- Any node whose title is not in the list above
- Any input field not explicitly listed above

---

## Checklist: Adding a New Workflow

- [ ] `Save Image` node present and titled exactly `Save Image`
- [ ] At least one `KSampler` node (any title)
- [ ] At least one latent or canvas node for dimension injection (`EmptyLatentImage` recommended)
- [ ] If using THM model control: checkpoint node titled `MainCheckpoint`
- [ ] If using THM LoRA injection: LoRA loader titled `MainLora`, wired directly from the checkpoint, with no pre-baked LoRA entries
- [ ] If using THM prompt control: prompt node titled `MainPrompt`
- [ ] If using THM negative control: prompt node titled `MainNegPrompt`
- [ ] If using pose/reference images: `LoadImage` node titled `MainImageAndMask`
