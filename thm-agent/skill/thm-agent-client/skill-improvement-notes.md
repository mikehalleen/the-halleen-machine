# Skill improvement notes — thm-agent-client

Staging file for gaps discovered during real project work. Reviewed and merged into
the canonical guides periodically (by the user, or by an agent when explicitly asked) —
**not** auto-merged. Writing here is not the same as the gap being fixed. Continue with
`-2`, `-3` suffix files rather than overwriting this one.

---

### 1. CLI interpreter resolution does not find a `venv/` install — and there is no env-var override
The skill (SKILL.md Environment table) says "`cli.py` resolves the right interpreter
automatically" / "auto-resolves this for you." That is only true for two cases. Reading
`thm-agent/client/config_helpers.py::resolve_agent_python` (called at `cli.py` startup,
line ~529): priority is **(1)** `config.toml [agent].python`, **(2)** a repo-root venv
named exactly `the-machine-ui-venv/`, then **(3) fail fast** — it explicitly never falls
back to `sys.executable`, and there is **no `venv/` / `.venv/` fallback and no environment
-variable override**.

On this install `[agent].python` was empty and the venv is named `venv/`, so
`python thm-agent/cli.py validate <project>` died immediately with
`ERROR: THM agent Python not found. Set [agent].python in config.toml ... or create
the-machine-ui-venv at the repo root.` — before validating anything. I had to validate by
importing `builder` directly with `./venv/Scripts/python.exe` instead. To make the CLI's
**generation/pipeline** path work at all (it spawns `scripts/run_images.py` / `run_video.py`
as subprocesses and needs an interpreter path for them), one of the two supported mechanisms
is required, and **both write `[agent].python` into config.toml** (set it directly, or run
`setup.py` which auto-detects and writes it). The skill should: (a) stop implying the CLI
self-resolves on every install, (b) state the exact priority order and the `the-machine-ui-venv`
name requirement, and (c) note that enabling CLI generation on a `venv/`-named install
requires either editing `config.toml [agent].python` or creating `the-machine-ui-venv`.

**Target doc:** SKILL.md § Environment (THM repo — no venv question) + generation-guide.md pre-flight.

---

### 2. LTX-2 i2v workflows are audio / lip-sync capable (dialogue in the prompt)
The video guidance treats LTX purely as start-frame motion (e.g. WORKFLOWS.md frame-support
table lists LTX i2v as start-only) and never mentions that LTX-2 i2v generates **spoken audio
and lip movement from dialogue text placed in the prompt**. The user corrected me on this, and
inspecting the workflows confirmed it: both `THM_video_ltx2_i2v.json` and
`DEV_LTX_FLF_video_audio.json` contain `LTXVAudioVAEDecode`, `LTXVConcatAVLatent`,
`LTXVEmptyLatentAudio`, `LTXVSeparateAVLatent`, and an audio-video text-encoder loader. So for
a talking-to-camera UGC spot, the creator's lines go into the video `inbetween_prompt` (which
THM injects into `THM-Prompt`) and come out as lip-synced audio — no separate lip-sync workflow
or VO step needed. The skill currently leads agents to wrongly tell users LTX can't do lip-sync.

**Target doc:** generation-guide.md (video) + schema-reference.md § Video / WORKFLOWS.md LTX rows.

---

### 3. ComfyUI "Save" vs "Save (API Format)" — UI-format workflows scan as fully broken
When the user pointed me at LTX workflows living in `D:/ComfyUI/user/default/workflows/`, the
THM video scanner returned **everything** missed/unsupported (no start/end frame, no save node,
no sampler, no tags) — which reads like a broken or wrong workflow, when the real cause was that
those files were saved in ComfyUI's **UI/graph (litegraph) format**, not the **API/prompt format**
THM's scanner and `run_video.py` require. Fast diagnostic: if the JSON has top-level `nodes`
(array) and `last_node_id`, it is UI format and THM cannot drive it; API format is a flat dict of
numeric node-id keys each with `class_type`. The fix is to re-export from ComfyUI via
**Save (API Format)** (and apply THM tags). Once the user dropped the API export into the repo
`workflows/` folder, the same file scanned correctly (`THM-StartFrame`, `THM-SaveVideo`,
`THM-FrameRate`, seed all found). Worth a one-line diagnostic in the docs so agents don't chase
a phantom capability problem.

**Target doc:** generation-guide.md pre-flight / WORKFLOWS.md (BYO workflow prep).

---

### 4. No-LoRA character consistency = klein reference-plate path (state it as THE answer)
When a user wants a consistent on-camera character but has **no character LoRA**, the answer is
not obvious from the docs. The asset-library-guide covers reference plates and the Custom family
binds `imageN`, but nothing frames the reference-plate workflow as *the* substitute for a missing
identity LoRA. The concrete, working recipe: generate one "hero" portrait (klein workflow with no
reference bound = effectively txt2img), pin it as the character's `reference_image`, switch to
**Custom image family** with `klein_multi_image.json` (4 `THM-ImageReference` slots, node-ids
198/213/218/224 in this build), and bind the hero plate as `image1` on every keyframe so Klein
carries identity across shots. Honest caveat to give the user: reference-based identity is strong
but not LoRA-perfect — minor drift between shots is possible (and UGC handheld/varied-angle framing
hides it well). This came up directly: user asked "I don't have any character loras, how are we
going to have consistency?" and the docs had no crisp answer to point to.

**Target doc:** asset-library-guide.md § Creator recipe (add a "no LoRA?" lead-in).

---

### 5. BUG: `src/helpers.py::load_config()` silently drops the `[agent]` section, breaking `[agent].python`
This is the root cause behind note #1, confirmed by reading source. `resolve_agent_python()`
(`thm-agent/client/config_helpers.py`) reads the configured interpreter via
`load_config().get("agent")`. But `load_config()` in `src/helpers.py` (lines ~462–494) does **not**
return the parsed TOML — it constructs a **new, fixed-shape dict** containing only `comfy`,
`paths`, `models`, `advanced`, `backups`, and `features`. The `[agent]` table is never copied in,
so `cfg.get("agent")` is **always `None`** regardless of what the user puts in `config.toml`.

Net effect: the entire `[agent].python` mechanism is dead through this code path. A user can set
`[agent].python = "venv/Scripts/python.exe"` (correct, resolves to a real file) and the CLI still
dies with `THM agent Python not found`, because `resolve_agent_python()` never sees the value and
falls through to the `the-machine-ui-venv/` convention (also absent on a `venv/`-named install).
Verified this session: with `[agent].python` set, `cli.py validate` still failed, and a direct call
to `resolve_agent_python()` raised, while `load_config().get("agent")` returned `None`.

Root cause is in `src/` (out of scope to edit from the agent fork). Two legitimate fixes:
**(A)** a fork-local patch in `thm-agent/client/config_helpers.py` that reads `[agent].python`
directly from `config.toml` via `tomllib` instead of through the lossy `load_config()`; or
**(B)** add `"agent": toml_data.get("agent", {})` to the dict `load_config()` builds (the proper
src-side fix — needs explicit user approval since `src/` is out of scope). Until fixed, the only
working interpreter mechanism is the `the-machine-ui-venv/` folder convention.

**Target doc:** This is a code bug, not a doc gap — flag for a real fix in `src/helpers.py::load_config`
(and/or harden `resolve_agent_python` fork-side). Note in generation-guide.md pre-flight until fixed.

---

### 6. Frame-only video QC overstated confidence on LTX clips (audio/lip-sync blind spot)
During a full-auto run I QC'd all five LTX video clips by extracting start/mid/end frames with ffmpeg
and reading them, then declared each a "pass" and selected it. The agent **cannot hear audio**, so the
entire spoken-VO and lip-sync dimension was unverifiable — yet for an LTX-2 dialogue-driven UGC spot,
audio/lip-sync is arguably the *primary* output, not a side channel. The user reviewed in the UI and
reported **significant problems** that frame QC had completely missed. Two failures to record:

(a) **Process/wording:** calling a clip a "pass" when a first-class dimension of it is unverifiable is
overclaiming. Frame QC should be reported as "visual framing/motion looks OK; audio + lip-sync NOT
verified by me" and clips with un-QC'able critical dimensions should be staged for **mandatory user
review before selection**, not auto-selected. For audio-bearing workflows (LTX-2 with dialogue), the
agent should arguably *not* auto-select at all and instead hand every clip to the user.

(b) **Likely technical gap (to confirm):** dialogue was injected by appending `She says: "..."` /
`Voiceover: "..."` to the video `inbetween_prompt` (which THM routes to `THM-Prompt`). It is unconfirmed
that this is the correct way to feed spoken lines to an LTX-2 audio/lip-sync graph — the audio text may
need a specific format, a dedicated node/field, or different phrasing, and the speaker-tag prose may be
getting voiced/garbled.

**Actual failure modes the user found in the UI (frame QC missed all of these):**
- **Minimal/late motion (clips 1,2,3,5):** end frame nearly identical to the start; when motion existed it
  started late with a large freeze-frame portion. LTX i2v over-anchors the start keyframe unless the motion
  prompt front-loads movement ("already moving from the first frame") and demands continuous motion across
  the whole clip. 3-frame QC (start/mid/end) can't catch this — need many frames or per-frame diffing.
- **Incoherent motion (clip 4, the faceless B-roll):** jumpy, no consistent action — two hands manipulating
  small objects is too complex; needs one slow, single, deliberate motion.
- **Inconsistent voice across clips:** each clip generated a different voice (varying accents); the faceless
  clip even produced a **male** voice. Fix direction from user: describe the speaker's voice explicitly and
  identically in every clip's prompt (e.g. "young American woman, warm upbeat voice, neutral accent"),
  especially the no-face clip where there's no visual cue to gender the voice.

So LTX-2 dialogue audio DOES respond to a voice description in the prompt, and without one the voice is
unstable per-clip — this is a required, not optional, part of authoring LTX-2 dialogue.

**Resolution attempts (motion) — what did and did NOT work:**
- Rewriting `inbetween_prompt` to front-load motion ("already moving from the first frame", "continuous
  motion for the entire clip") + motion negatives: **did NOT fix it.** Clip stayed frozen ~¾ of the way,
  then crammed all motion into the final frames as a violent blur.
- Raising `inbetween_generation.fps` 16 → 24 (LTX-2 is ~24–25fps native, not 16): **removed the end-smear
  blowout** (keep this — 16fps was wrong for LTX-2), but motion stayed minimal/near-static.
- Root cause is workflow-side and NOT agent-controllable from the project: this LTX workflow's second
  sampler pass uses a baked `ManualSigmas` of `0.909375, 0.725, 0.421875, 0.0` (3 steps, starting at
  sigma ~0.9). For i2v, a 3-step schedule starting low barely denoises away from the conditioning start
  frame, so the result stays glued to the keyframe = near-static. `THM-Steps` is absent on this workflow
  (scan: `project_steps=no`), so project video-steps can't help; the sampler/sigmas must be fixed in the
  ComfyUI graph (more steps / fuller sigma schedule / lower i2v conditioning strength) or a different LTX
  i2v workflow supplied. Lesson: **for LTX BYO workflows, scan/inspect the baked sampler (steps + any
  `ManualSigmas`) BEFORE a full run** — a too-short baked sigma schedule produces static i2v that no prompt
  or fps change can rescue, and frame-only QC won't reveal it until you diff many frames.

**CONFIRMED FIX (this matters):** the real lever was the **`LTXVImgToVideoInplace` `strength`** parameter,
which was **1.0** on both the base and refine i2v conditioning nodes. "Inplace" strength 1.0 pins *every*
latent frame to the conditioning image, freezing motion. Lowering it to **0.5** (workflow edit, both nodes)
produced genuine talking-head motion — mouth/expression change, head/hair movement, framing drift, no
freeze, no end-smear — while fully preserving identity. So static LTX i2v is NOT unfixable from outside the
sampler internals: check `LTXVImgToVideoInplace`/`LTXVImgToVideo` `strength` first; ~0.5 is a good
motion/identity balance, lower adds motion but risks identity drift. **fps correction:** I first thought
fps 16→25 was needed to kill an end-frame smear, but that smear was ALSO a `strength=1` artifact —
`strength=0.5` alone fixed both the static motion and the smear at fps 16. fps 25 was then actively
harmful: at 25fps the frame counts (e.g. 7s×25 = 175 frames) made renders 6–12× slower (27–60 min vs
~3.5 min) and caused intermittent **silent VRAM failures** (no mp4 written) on the heavier clips. Keeping
fps **16** with strength 0.5 gave good motion, no smear, and reliable ~3.5-min renders. Lesson: fix motion
with `strength` first, and don't raise fps to chase artifacts — higher fps mainly buys frame-count cost and
instability here. (Always back up the workflow before editing — saved to
`THM_video_ltx2_i2v.ORIGINAL.bak.json`.)

**Target doc:** generation-guide.md / WORKFLOWS.md (LTX BYO pre-flight: inspect baked sampler steps +
ManualSigmas; set fps to LTX-native ~24–25; **lower `LTXVImgToVideoInplace.strength` (~0.5) to fix static
i2v** — this is the primary motion lever, not the prompt).

**Target doc:** vision-qc-guide.md (add: audio-bearing video clips cannot be QC'd by the agent —
never auto-select; hand to user) + generation-guide.md / prompt-writing-guide.md (correct way to author
LTX-2 dialogue for audio + lip-sync, once known).

---

### 7. When `cli.py` is interpreter-blocked, drive the runner API directly (it uses `sys.executable`)
The interpreter bug (#1/#5) blocks **every** `cli.py` subcommand because the guard runs at startup — so
`validate`, `comfy-status`, `generate`, `pipeline` all die before doing anything. But generation does not
actually require `cli.py`. The runner API in `thm-agent/client/runner.py` runs in-process —
`run_keyframe(data, seq_id, kf_id, seed=)`, `run_video(data, seq_id, vid_id, seed=)`,
`run_asset(data, asset_type, asset_id, seed=, layout_override=)`, `validate_video_prerequisites(...)` —
and its `_run_script()` launches `run_images.py` / `run_video.py` with **`sys.executable`**, i.e. whatever
interpreter is running the runner. So a small driver script executed with the correct venv python
(`venv/Scripts/python.exe`) generates fully, bypassing the broken resolver with **zero config/code/junction
changes**. This is what unblocked the entire full-auto run this session. The skill says "you run all CLI"
via `cli.py`; it should document this runner-direct fallback for installs where `cli.py` can't resolve.

**Target doc:** generation-guide.md (add a "CLI blocked? drive the runner API directly" fallback).

---

### 8. Headless asset generation lands in a throwaway test-cache path — library copy + pin is a manual step
`runner.run_asset` (and `cli.py generate asset`) write the generated image to
`{output_root}/__test_cache_setting__/...` (per-type test cache), **not** to the canonical
`{output_root}/{project}/_locations|_characters/{id}/gallery.png`, and they do **not** set the asset's
`reference_image`. The Gradio "save to library" action does both automatically; the runner does neither.
So an agent generating assets headlessly must, after QC: copy the result to the next free `gallery[_N].png`
and patch `reference_image` itself (then it's usable for keyframe bindings). The asset-library-guide's
"Save library recipe" implies a copy step but doesn't warn that the raw generate output is in a disposable
test-cache folder, which is easy to mistake for the real library path.

**Target doc:** asset-library-guide.md § Save library recipe (note the test-cache output path explicitly).

---

### 9. Single-shot talking-head / sign-off clips need an explicit in-frame hold (open_end permits exit)
Schema-reference presents the frame-hold clauses as a *chained-oner* concern, but they apply to single-shot
clips too. Shot 5 (one keyframe, `open_end` F/T, a sign-off) had the subject turn the selfie camera away
and walk out of frame, ending on an empty storefront under the "okay bye" VO — because `open_end` *permits*
exit and the motion prompt ("turning… walks out") invited it. The fix that worked: rewrite `inbetween_prompt`
to "stays fully in frame the entire clip, keeps the camera on her face, does not turn the phone away" plus
negatives (`subject exits frame, person walks out of frame, empty frame, camera turns away from her face,
back of head`). Lesson: for any talking-head/sign-off beat, add an explicit in-frame hold even on a single
open-end shot — don't assume the subject stays centered.

**Target doc:** prompt-writing-guide.md § Frame-hold continuity (extend to single open-end talking-head shots).

---

### 10. Action B-roll keyframes must anchor the BEFORE state, not the completed action
For a start-frame-conditioned action clip (e.g. snapping the puck onto the phone), the keyframe is the
**first** frame, so it must depict the **pre-action** state and leave the motion for the video. The first
build of Shot 4's keyframe described "hands snapping the puck onto the back of a smartphone," which produced
an ambiguous still with the action already implied and no room for the snap to play out. Rebuilding the
keyframe as "puck held a few inches away, not yet attached, clearly separate from the phone" gave a clean
start frame and let the video perform the actual snap. General rule: still anchor max-fidelity detail in the
keyframe, but stage that keyframe at the **start** of whatever motion the following video must perform.

**Target doc:** prompt-writing-guide.md (keyframe vs in-between: stage action keyframes at the start state).

---

### 11. Pre-flight disk check on BOTH drives; clean reference plate does NOT prevent in-motion text hallucination
(a) **Disk:** before a long auto-run, check free space on **both** the output drive and the **system** drive.
Mid-run the system drive (C:) hit 0 bytes while the output drive (D:) was fine; this silently broke the
agent's own shell output capture (tool temp files live on C:) and would have jeopardized the video renders.
A free-space pre-flight belongs in the long-run playbook, not just an output-drive assumption.
(b) **Text hallucination confirmed:** even with a *clean, unbranded* product reference plate, LTX painted
gibberish "branding" onto the puck's matte face in a close-up video frame. A clean plate does **not** prevent
in-motion text hallucination on the object, and re-rolling does not reliably remove it — confirms (and
sharpens) prompt-writing-guide § "Text hallucination on large plain surfaces."

**Target doc:** generation-guide.md § Long-run playbook (disk pre-flight) + prompt-writing-guide.md § Text
hallucination (clean plate is not a guard for video).

---

### 12. Never clear `selected_*_path` to empty — it breaks the UI; keep JSON integrity, swap only after the new file exists
When rewriting the five video prompts before regenerating, I set each `selected_video_path = ""` to "mark"
the clips as needing a re-render. This was wrong and the user hit **UI errors about empty selected-video
values**. Two mistakes: (a) I emptied valid pointers *before* any replacement render existed, and (b) an
empty `selected_video_path` (and equally `selected_image_path`) violates what the UI expects — the app
errors on blank selection fields rather than treating blank as "unselected." Clearing is not a harmless
"needs regen" flag.

**Rule:** treat `selected_image_path` / `selected_video_path` as fields that must always hold a valid,
on-disk path once set. To regenerate, **leave the existing selection in place**, generate the new output,
then **swap** the field to the new file (and only then is the old one safe to discard). Never blank them as
an intermediate step. If you truly need a "stale, pending re-render" marker, use a separate field/note — do
not repurpose the selection field by emptying it. This complements schema-reference's "do not overwrite
selected_*_path unless the user asks": don't *clear* them either. Same caution applies to any required-
populated field the UI reads (keep the JSON valid for a UI that may be open and re-reading it live).

**Target doc:** schema-reference.md § "Agent: do not overwrite on re-edit" (add: do not *clear* selection
fields either — keep a valid path; swap after the new render exists) + round-trip editing section.

---

### 13. `run_video` reports false success when SaveVideo writes nothing — verify output mtime, not just `success`
In a background batch, two of four clips (the heaviest, at fps 25) ran for 27–60 minutes, then reported
`success: true` with a `main_path` — but **no new mp4 was written**. The folders showed only the OLD mp4
(stale timestamp) plus a fresh `debug_workflow_iter.json`, proving the graph executed but the video save
failed (silent VRAM exhaustion at high frame counts). The cause: `runner.run_video` derives success from
`_find_latest_image(dir)` returning *any* file, so when the new render produces nothing it returns the
pre-existing file and reports success. An agent QC'ing by "did it report success + is there a path" gets
fooled into selecting a stale clip.

**Rule:** after any generate, verify the returned file's **mtime is newer than the run start** (or capture
the expected new `_0000N_` filename and confirm it exists), not just that `success` is true and a path came
back. Especially for long/heavy video renders where partial OOM can swallow the SaveVideo step. Also: run
heavy video clips **one at a time**, not in a back-to-back batch — failures clustered on the later clips,
consistent with accumulated VRAM/memory pressure across sequential runs.

**Target doc:** generation-guide.md § Long-run playbook (verify output freshness; serialize heavy renders)
+ consider hardening `runner.run_video` to compare against pre-run dir contents (fork-side fix candidate).

---

### 14. Verify asset attributes from the pinned reference image — don't describe products from assumption/memory
I repeatedly wrote "matte charcoal puck" in prompts and then "flagged a bug" when Wan rendered the product
**pink with a white V logo** — but the project's pinned `Voltpuck` reference asset (`_characters/<id>/
gallery_3.png`) IS a pink puck with a white V. There was no drift: reference → keyframes → video were all
consistent; my charcoal description was a hallucinated assumption that contradicted the actual asset. I
nearly sent the user to regenerate keyframes + re-render videos to "fix" correct output. (The i2v step
follows the keyframe/reference, so my wrong color text didn't even take effect — which is exactly why the
mismatch was mine to catch, not the model's.)

**Rule:** before describing or QC-judging any asset's attributes (color, logo, shape, wardrobe), open its
`reference_image` and look — treat the pinned asset as ground truth, not your memory of earlier prompts.
When QC reveals a mismatch between output and your expectation, check the reference BEFORE concluding the
model drifted; the bug may be in your assumption. Also: a product/character prompt should echo the
reference's real attributes (or stay neutral), never invented ones.

**Target doc:** vision-qc-guide.md (QC against the pinned reference, not assumptions) + prompt-writing-guide.md
(describe assets from their reference image; verify before claiming drift).
