# Worked example — from brief to generation, the whole method once

One end-to-end pass through the planning method on a generic brief. Read it once before your first project; every step links to the canonical doc that goes deeper. Nothing here references any real project — the brief, workflow, and assets are invented.

**The brief (from the user):**

> "Here's a photo of my product. Composite it into a nice scene, then animate a slow push-in that lands on the product. I have my own multi-reference workflow — `my_edit_flow.json`."

---

## 1. Map the brief onto THM primitives

Before touching JSON, decompose the request ([schema-reference.md](schema-reference.md) § THM primitives): *which moments are anchors, what moves between them, which identities recur?*

| Brief element | Primitive |
|---------------|-----------|
| "my product" (photo supplied) | **Asset** — a character entry with the photo pinned as `reference_image` |
| "a nice scene" | **Asset** — a setting entry; the scene plate is reviewable and reusable, not throwaway prompt text |
| "lands on the product" | **Keyframe** — the landing close-up is the frame that must be pixel-exact |
| "slow push-in" | **Video** — motion *into* the anchor |
| One product, one shot | **Sequence** — a single discrete cut (`build_shots`, one shot) |

## 2. Anchors decide the structure

The fidelity rule ([schema-reference.md](schema-reference.md) § Keyframe `layout`) is a **planning input**: the moment that must be exact becomes the keyframe. Here that is the **landing** — the product filling the frame at the end of the push-in.

Derive `video_plan` from the wording ([prompt-writing-guide.md](prompt-writing-guide.md) §5 § Derive the plan from the request wording): *"lands on"* → **`open_start: true, open_end: false`** — one keyframe (the landing), one video that generates **into** it.

> **Trap:** the user said "composite it, *then* animate" — narration order, not timeline order. The keyframe is the *last* frame of this clip, not the first.

## 3. Introspect the unfamiliar workflow

`my_edit_flow.json` is the user's own — assume nothing. Run the procedure in [lookup-reference.md](lookup-reference.md) § Introspecting a workflow you've never seen:

1. Confirm it's an API export (flat node-id-keyed dict).
2. Enumerate `LoadImage` reference slots and `THM-*` control nodes — suppose it reports two reference slots, node-ids `"31"` and `"58"`.
3. Check the **video** workflow's end-frame support: the plan above needs an open-start (landing) clip, which requires end-frame conditioning. The default family (`i2v_base.json`) supports it; a start-only BYO video workflow could not land on a keyframe at all — that check happens **now**, not after a failed render.
4. Remember: reference inputs are optional capabilities, and `imageN` indices renumber to the **active** bindings in wiring order.

## 4. Create the project — family resolved up front

The brief already implies Custom (reference-plate compositing), so pass it at creation and set the chosen workflow immediately — never leave the `create_blank` placeholder keyframe pointing at a workflow the user didn't choose ([SKILL.md](SKILL.md) must-read #3):

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path("thm-agent").resolve()))
import builder

data = builder.create_blank("push-in-demo", family="custom")
data = builder.patch_field(data, "project.default_workflow_json", "my_edit_flow.json")
builder.save_project("{workspace}/push-in-demo.json", data)   # save root from config.toml [paths].workspace
```

## 5. Asset phase — plates before keyframes (check in with the user)

Two assets: the **product** (photo pinned as its plate) and the **scene** (generated as a setting plate, reviewed and approved before any keyframe uses it).

```python
data, product_id = builder.add_character(data, "hero-product", "matte ceramic vase, narrow neck, speckled glaze")
data, scene_id   = builder.add_setting(data, "display-scene", "sunlit shelf corner, plaster wall, soft morning light")
```

Generate the scene plate via the asset path, show it, pin the approved image as the setting's `reference_image`. Asset generation may run a different model family than the keyframes — e.g. SDXL (+LoRAs) for plates, the multi-reference workflow only for compositing ([asset-library-guide.md](asset-library-guide.md) § Mixed-model pipelines).

> **Check-in:** the workflow *could* invent the scene inline in the keyframe prompt instead. That skips plate review and kills reuse — it's a valid user choice, not the default. Ask: *"Want the scene as its own reviewable plate first (reusable across shots), or generated inline in one step?"* ([asset-library-guide.md](asset-library-guide.md) § Phase order.)

## 6. Build the shot, then wire bindings explicitly

```python
from builder import ShotSpec

shot = ShotSpec(
    layout="tight close-up of the ceramic vase image1 centered on the shelf image2, filling most of the frame",
    inbetween_prompt="slow steady push-in toward the vase, settling as it fills the frame",
    duration_sec=5,
    open_start=True, open_end=False,        # landing keyframe — derived in step 2
)
data, seq_ids = builder.build_shots(data, [shot])   # clears the create_blank placeholder automatically
```

`build_shots` does **not** wire Custom-family references — `ShotSpec.character_ids` is Default-family only ([schema-reference.md](schema-reference.md) § Builder API). Bind the plates per keyframe, using the node-ids found in step 3:

```python
seq = data["sequences"][seq_ids[0]]
kf = seq["keyframes"][seq["keyframe_order"][0]]
kf["workflow_json"] = "my_edit_flow.json"
kf["reference_bindings"] = {
    "31": {"semantic": "character", "character_id": product_id, "reference_image": "<product plate path>"},
    "58": {"semantic": "location",  "source": "sequence",       "reference_image": "<approved scene plate path>"},
}
builder.save_project("{workspace}/push-in-demo.json", data)
```

With both slots active, `image1`/`image2` in the layout match wiring order; verify against the `[REF] prelude` log lines on the first generation. Run `validate` before generating.

## 7. Generate in order

1. **Keyframe** (the landing) → show the user → `select keyframe` on approval.
2. **Video** (the push-in into it) → QC that the motion arrives at the anchored frame → `select video`.

The landing is guaranteed faithful because it *is* the approved keyframe — the video only has to travel to it.

---

## Why not the three wrong shapes

| Wrong shape | What goes wrong |
|-------------|-----------------|
| **Start-only** (`open_end: true`, keyframe first) | The landing close-up — the one frame that matters — is invented by the video model: drift, reinterpretation, wrong framing. |
| **KF→KF with two independently generated anchors** | Two separate generations never pixel-align; the model reconciles the differences by morphing/dissolving mid-clip ([schema-reference.md](schema-reference.md) § Two-anchor controlled interpolation — anchors must match element-for-element). |
| **Scene invented inline in the keyframe prompt (silently)** | Renders fine, but the location is never a reviewable plate and can't be reused or kept consistent across shots. Fine if the user chooses it — ask, don't default to it. |
