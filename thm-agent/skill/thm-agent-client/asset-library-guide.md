# THM agent — asset library guide

Generic workflow for building **creator**, **product**, and **location** assets before keyframes or video. Use this when the user is in an **asset phase** — not when storyboarding shots.

See also: [generation-guide.md](generation-guide.md) (CLI, mirror, gallery), [anti-patterns.md](anti-patterns.md) (what not to do).

---

## Host clone — new story, same look

When the user wants a **fresh storyboard** but keep all project-level work from an existing project:

| Step | Who | Action |
|------|-----|--------|
| 1 | User | Start a **new agent** (not the host project's agent) |
| 2 | User | Names host path and approves clone |
| 3 | New agent | `clone-from-host --source samples/host.json --name new-story --approve` |
| 4 | New agent | Binds to `samples/new-story.json` for all future work |

**What transfers:** `characters`, `settings`, `styles` (full objects including `reference_image` and pinned paths), models, LoRAs, generation globals, tone/style fields.

**What starts blank:** `sequences`, `sequence_order`, keyframe selections, poses, per-beat bindings.

**Host file is never modified.**

**Clone copies JSON pointers, not files.** `clone-from-host` deep-copies the project dict — including `reference_image` paths — but never touches the filesystem. Pinned refs still point into the **host** project's output tree (`{output_root}/host/_characters\|_locations\|_styles\|_poses\`), non-pinned `gallery_N` variants are not copied, and any `_poses/` library is not copied. Until you fix this the clone is **coupled to the host**: if the host output is regenerated, moved, or cleaned, the clone's refs break or silently change content.

**Post-clone step (do this before ideation, don't wait to be asked):** tell the user what was *not* physically brought over — pinned reference files, their sibling `gallery_N` variants, and any `_poses/` files — and offer to copy them into the new project's already-scaffolded output folders, repointing each `reference_image` to the new path. Don't treat cloned assets as self-contained until this is done.

**Anti-pattern:** Host agent clearing `sequence_order` or rebuilding shots in the same JSON to simulate a new project.

THM has no first-class cross-project export UI; `clone-from-host` is the interim bridge.

---

## Phase order

Work in this order unless the user directs otherwise:

1. **Creator** — on-camera talent / UGC persona
2. **Product** — hero product (bottle, tube, package)
3. **Location** — store aisle, shelf, corner, spa, etc.
4. **Keyframes / video** — only when the user explicitly moves on

If the user says *"assets only"*, *"not keyframes yet"*, or similar — **stop** after location/product work. Do not generate seq/kf/video, do not propose storyboard shots, do not set `selected_image_path` on keyframes.

### Creator recipe

1. **Identity explore** — clear `reference_image`; distinct prompts; spread seeds → user picks face
2. **Refine** — pin winner; adjust prompt (e.g. matte skin + negatives); regenerate if needed
3. **Outfit** — pin face ref; vary outfit clause in prompt only → user picks one outfit for whole video
4. **Poses** — same outfit ref; `layout_override` for full-body framing (head to shoes, multiple angles)
5. **Save library** — copy to `_characters/{id}/gallery.png`, `gallery_2.png`, `gallery_3.png`; active pin on `gallery.png`

### Product recipe (hero as character)

1. **Bottle type** — clear `reference_image`; explore pump / tube / jar (etc.) → user picks shape
2. **Color** — pin shape ref; vary color clause → user picks palette
3. **Label / text** — pin color ref; 2–3 text treatments; strong negatives for gibberish/blur; legibility not guaranteed
4. **Save** — pin to `_characters/{product_id}/hero_product.png` (or `gallery.png` per project convention)

### Location recipe

1. **Archetype** — clear `reference_image`; explore environment types → user picks aesthetic
2. **Angles** — pin aesthetic; `layout_override` for down-aisle vs corner (etc.)
3. **Detail views** — pin corner (or primary); generate straight-on shelf with shelf-specific prompt + layout
4. **Save library** — typical mapping:
   - `gallery.png` — corner (or primary active ref)
   - `gallery_2.png` — straight-on shelf
   - `gallery_3.png` — down-aisle
5. Update sequence `setting_prompt` text to match pinned environment (not old placeholder wording)

**Vocabulary check:** *"straight"* often means **straight-on shelf** (camera facing products), not down-the-aisle. Confirm if ambiguous.

---

## Scope gate

| User says | Agent does |
|-----------|------------|
| *"Let's build the creator first"* | Asset tests for character only |
| *"Assets only for now"* | No keyframes, no video, no seq edits beyond asset test cache |
| *"Ready for keyframes"* / *"Let's storyboard"* | Switch to keyframe workflow in [SKILL.md](SKILL.md) |

---

## Explore vs iterate

| Goal | `reference_image` | Seeds |
|------|-------------------|-------|
| **New identity** — different face, archetype, store type | Clear or omit `reference_image` | Spread seeds (e.g. 10_000, 20_000, 30_000) |
| **Iterate** — outfit, color, pose, camera angle on same subject | Keep pinned `reference_image` | Spread seeds; prompt + seed are independent levers |

**Explore:** user wants options, not refinement of one look.  
**Iterate:** user approved a base; you're tuning details.

Never use consecutive seeds (1000, 1001, 1002) as a diversity strategy — they produce near-duplicates.

---

## Product-as-character

Hero products belong in **`characters[]`**, not `styles[]`.

- Create a character asset with a clear name (e.g. *Wonder Tube*, *Lotion Bottle*)
- Use **Custom** model family when multi-reference / gallery workflow is needed
- Gallery folder: `{output_root}/{project}/_characters/{asset_id}/`
- Pin approved still as `reference_image` on that character entry

`styles[]` is for look/grade/lens — not the product itself.

**Keep placement out of the asset prompt.** The product's own prompt describes only the object (form/material/color/texture). Where it sits is Setting scope → `setting_prompt` or the keyframe `layout`, not the character module:

- ✓ `ceramic teapot, curved spout, rounded handle, warm glazed finish`
- ✗ `ceramic teapot, curved spout… **displayed on a console shelf**` (shelf/wall/table is placement — scope bleed)

---

## Pin vs library

| Concept | Where | Purpose |
|---------|-------|---------|
| **Active pin** | `reference_image` on the asset in project JSON | What generation uses *now* |
| **Library** | `gallery.png`, `gallery_2.png`, `gallery_3.png` in `_characters/` or `_locations/` | Saved alternates for later keyframe binding |

**Pin** = update `reference_image` via `builder` after user approves.  
**Library** = copy approved file to canonical gallery name in output folder (in addition to pin when user wants alternates kept).

Confirm ambiguous terms before generating — e.g. *"straight"* may mean **straight-on shelf** (camera facing product) vs **down the aisle**.

---

## Never overwrite in-use gallery files

**Canonical library paths** (`gallery.png`, `gallery_2.png`, `gallery_3.png`, … under `_characters/` or `_locations/`) are **additive only** when the agent saves new variants.

| Wrong | Right |
|-------|-------|
| Copy a new test over `gallery_4.png` because it is already bound | Save as **`gallery_5.png`** (or next free name) and update JSON binding to the new path |
| Overwrite a gallery file to "pin" a winner during iteration | Leave existing pins untouched; add new file + bind |
| Assume user will delete the old file | Only the **user** deletes library or preview assets |

**Why (non-negotiable):** overwriting in-use gallery files has lost approved plates and broken trust — restores from backups were required in real sessions. Treat every bound path as immutable unless the user explicitly replaces it.

**Workflow:** user approves a new plate → copy to **new** canonical name → update `reference_image` or keyframe `reference_bindings` → confirm in plain language. Never destroy a path another binding still references.

Preview folder (`{workspace_root}/{project}-files/previews/`) follows the same rule — additive tiles only; agent never clears unless user explicitly asks.

---

## Preview folder vs canonical library

**Additive gallery:** preview tiles **accumulate** across sessions. Add new outputs with unique `--name` values; rebuild with `gallery --open`. **Only the user** deletes files from `{workspace_root}/{project}-files/previews/` — the agent must **never** run `gallery --clear` or `clear_preview_dir` unless the user explicitly asks to wipe the folder.

| Location | Role |
|----------|------|
| `{workspace_root}/{project}-files/previews/` | Short filenames for HTML compare (`creator-A.png`, `shelf-straight.png`) — **not** canonical; grows over time |
| `thm-agent/workspace/{project}/…` | Agent-readable mirror after each generate |
| `{output_root}/{project}/_characters\|_locations/{id}/` | Canonical library paths referenced in JSON |

Previews are disposable compare tiles. Library paths are what the project JSON references long-term.

---

## Batch compare recipe

For N variants in Assisted mode (user explicitly asked):

1. **Propose** batch in plain language — what you're testing, how many, why
2. **Generate loop** — one at a time in Manual; or N with user opt-in; mirror auto-attaches `workspace_path`
3. After each gen: `gallery --src WORKSPACE_PATH --name {group}-{variant}.png` (unique names — gallery is additive)
4. **`gallery --open`** — rebuild `index.html` and open in browser (same tab refreshes)

Between steps, set session hints so the user can re-orient (`--pending`, `--change`, `--group-note`, `--note` on each `--src`). Newest tiles appear first; latest gets a **Latest** badge. Do **not** clear the preview folder — only the user removes tiles. See [generation-guide.md](generation-guide.md).

Repeated `gallery --open` for the same project **reuses one browser tab** (`thm-gallery-{project}`) — the compare view refreshes in place.

Preview naming: `{group}-{variant}.png` → HTML groups by prefix (`creator-A` → group **Creator**, label **A**).

---

## Save library recipe

After user picks a winner:

1. Copy approved file (from workspace mirror, preview, or ComfyUI output) to canonical gallery name in output folder
2. Update `reference_image` on the asset via `builder.load_project` → patch → `save_project`
3. Confirm in plain language which asset is now pinned
4. Optional alternates: `gallery_2.png`, `gallery_3.png` in same folder — available in Gradio library; keyframe bindings may still point at `gallery.png` until shot-level wiring

**Workspace mirrors** survive under `thm-agent/workspace/{project}/…` with long filenames — use `*seed{N}.png` glob when preview copies are gone.

**One-off scripts** (`{project-name}-files/scripts/save_*_library.py`) are optional helpers; prefer documenting this recipe in the agent workflow over proliferating per-project scripts unless the user wants them kept. Keep them under `{project-name}-files/scripts/` — never `thm-agent/scripts/` or `thm-agent/` root.

---

## Layout override (poses, angles, shelf straight-on)

When default asset-test layout is wrong (pose test, straight-on shelf, aisle angle), pass a custom layout:

```bash
python thm-agent/cli.py generate asset \
  --project samples/my-project.json \
  --type setting --id UUID \
  --layout-override "straight-on product shelf, eye level, centered package" \
  --json
```

Use for setting/character asset tests where framing matters more than identity exploration.

---

## Open questions (defer to keyframe phase)

- Per-keyframe different location refs may need `reference_bindings` updates when storyboarding starts — library is built in asset phase; bindings wired per shot later
- Product label legibility is prompt-limited — do not promise readable text without user accepting tradeoffs
- HTML gallery opens in the **default OS browser**, not an IDE's embedded preview

---

## Known limits

- Workspace mirror filenames may stay long; previews use short names
- No cross-project asset reuse — each project has its own IDs and gallery paths
- Agent **Read** of `workspace_path` is for agent vision; user QC uses **`gallery --open`** (preferred — never links in chat); `open`/`reveal` only if user asks
