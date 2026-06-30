# THM agent — vision QC guide

Quality control is **agent vision** — you view generated images/videos and judge them against **story requirements**. Do **not** use JoyCaption, `scripts/qc/`, or local ML scorers.

**Default mode is Manual.** Vision QC is a **conversation tool**, not an autonomous loop. **User must view output via OS/browser** before you treat anything as approved.

---

## Agent QC vs user QC

| Who | How | Purpose |
|-----|-----|---------|
| **User** | **`gallery --open`** (HTML preview — preferred); `open` / `reveal` only if user asked | Approval, pick winner, pass/revise |
| **Agent** | Read `workspace_path` from CLI JSON | Vision notes, layer mapping, patch proposals |

Do **not** assume chat Read or an IDE preview pane shows images to the user. Agent Read is **not** a substitute for user viewing.

After every generate: **`gallery --src … --name … --open`** for the user — **never** paste image links in chat. Then offer QC notes if helpful.

---

## Manual mode (default) — how QC actually works

**User is ground truth; agent vision is advisory.** Do not treat agent Read or image descriptions as approval.

1. User approves or requests **one** generation  
2. You run it, **`gallery --open`** for user (HTML preview), and **stop** — never links in chat  
3. User reacts — pass, revise, try again, or pick direction  
4. **Good enough or variant picked → stop that beat** — do not regen unless they ask  
5. **User interrupt → hard stop** — no loops or regens in the same turn  
6. Only then: optional brief QC notes (agent Read), prompt patch proposal, or a **proposed** single regenerate  
7. Never auto-chain QC → patch → regenerate → select → video  

**Do not** rank variants, pick winners, or call `select keyframe` in Manual mode unless the user explicitly asked for variant comparison or told you which file to select.

---

## What to compare each output against

When the user wants your read, or in Assisted/Semi-auto with their opt-in:

1. **Storyboard row** for that beat — KF layout + video motion intent  
2. **Project JSON prompt layers** — character/setting/style/layout/inbetween fields  
3. **User-stated requirements** — e.g. "close-up", "readable label", "green dino ahead"  
4. **Continuity** (when user cares) — describe visible match to prior approved frames; do not rely on "as before" in prompts  
5. **Anchor-based cross-frame consistency** (video frame-strip QC) — pick one fixed architectural element visible across the sampled frames (e.g. a ceiling light panel) and check whether **other** elements hold a consistent spatial relationship to it frame-to-frame; separately, track one object's size/shape across frames rather than judging each frame in isolation. This is how a real background-rigidity bug (furniture relocating, objects drifting relative to fixed features — see [prompt-writing-guide.md](prompt-writing-guide.md) § Background rigidity breaks down during deep depth-traversal) was actually caught — judging each frame independently missed it.

Load context only for the **user-named** project:

```bash
python thm-agent/cli.py summarize samples/my-project.json
```

Read the generated file at `workspace_path` (agent tool) **after** opening it for the user.

---

## QC output format (agent → user)

For each image or video frame reviewed, report:

| Field | Content |
|-------|---------|
| **Verdict** | Pass / Revise / Reject |
| **Deltas** | Specific visible problems — "subject too short", "wrong pants", "missing motion blur" |
| **Layer** | Which JSON field to change — `characters[].prompt`, `keyframe.layout`, `setting_prompt`, etc. |
| **Suggested edit** | Concrete replacement prompt text (scope-pure per prompt-writing-guide) |

Example:

```
Verdict: Revise
Deltas: Shopper reads as too short; pants are denim not khaki
Layer: characters[].prompt (height/build), keyframe.layout if pose wrong
Suggested: "early 30s person, tall build, long legs, khaki chinos with creased knees, ..."
```

In **Manual** mode, present QC **in common language after** the user has viewed the output — not as a substitute for opening it, and not as a pre-approval script dump.

---

## Author a project anti-pattern checklist (practice, not a one-off)

At storyboard time — before generation starts — write a short checklist of **known failure modes for this project's specific motion types**. This is a general, repeatable practice, not a dino-specific artifact. A real worked example (one project's actual list, for reference on shape/granularity, not as a universal list to copy verbatim):

- backward run / moonwalk
- moving obstacle (should be static until impact)
- prop morphing
- slow trot / unwanted deceleration
- missing crash burst
- screen direction flip
- wrong character count
- keyframe/video cut mismatch
- obstacle present on both bracketing keyframes (should be intact on one, post-impact on the other)
- character rendered inside an intact structure that should block them

**Score every frame against the project's own checklist during QC** — not just against general "does it match storyboard intent." The checklist should be specific to the motion types *this* project actually uses (sprints, falls, rotations, depth-traversal, product reveals, whatever applies) — write it once per project at storyboard time, reuse it for every QC pass in that project.

---

## Automation levels

**Default: Manual.** Higher levels require explicit user opt-in for that session or task.

| Level | When to use | Agent behavior |
|-------|-------------|----------------|
| **Manual** (default) | Always, unless user opts up | One gen → **`gallery --open`** → **wait** → discuss → propose next single step |
| **Assisted** | User says e.g. *"try 4 variants"* | Generate N → **gallery --open** → **wait for user pick** → then `select keyframe` |
| **Semi-auto** | User says e.g. *"iterate up to 3 times"* | QC → propose patch → **ask** → one regenerate per approval; stop at K or when user says stop |
| **Continuous forward** | User says *keep going*, *don't stop*, *run forward* | Skip-complete beats; no per-sequence re-approval; additive gallery; interrupt kills runners |
| **Unattended** | User explicitly opts in (*"overnight"*, *"full trust"*) | Phase 1: detached gen + agent vision QC at scale; Phase 2: manual on failures only |

If the user has not named a level, **assume Manual**. Do not infer Assisted from "generate a keyframe" or "let's try this shot."

---

### Conservative motion as the default for automation

When a motion type is **known high-risk for this pipeline** for a given project (e.g. significant depth-traversal through a parallax-heavy background, or rotational/acrobatic character motion — see [prompt-writing-guide.md](prompt-writing-guide.md) §2 principle 8 and § Background rigidity breaks down during deep depth-traversal), an **automated or first-pass run should default to the safer, lower-risk version** of that beat even though it's less dynamic. Reliability over ambition when nobody is watching each individual generation.

The more ambitious, artifact-prone version (deeper travel, a compound rotation, etc.) should be a **manual escalation the user deliberately reaches for afterward** — not the thing the pipeline or agent reaches for first in Assisted/Semi-auto/Unattended tiers. This applies to storyboard-time choices (which `video_plan` shape, how much depth a beat travels) as much as to in-session prompt revision.

---

## Automated QC rules (Unattended Phase 1)

When user opts into unattended automation, automated QC is **computer vision only**:

| Rule | Detail |
|------|--------|
| **Never file size** | Do not rank or pick by largest file, resolution proxy, or any non-vision heuristic |
| **Primary criterion** | Does the image match **storyboard intent** (layout, beat role, bindings)? |
| **Secondary** | Prompt adherence — informative, not decisive |
| **Global tone gates** | e.g. realistic project → reject incorrect anatomy even if intent otherwise met |
| **Early stop** | Stop generating after first **pass** — except variant **1** on a beat must run **at least 2** generations before stopping on pass |
| **Logging** | Record rationale per pick via `pipeline record-qc --rationale` — no image links in chat |

**Two-phase pattern:**

1. **Phase 1 — Full auto QC:** `pipeline keyframes` → agent Read each beat's variants → `pipeline record-qc` → `pipeline apply-selections`  
2. **Phase 2 — Manual guided:** `gallery --open` only for beats that failed Phase 1; user is ground truth  

Keep all variant files on disk for audit; gallery in Phase 2 only for failures.

---

## Multi-variant workflow (Assisted only — user must opt in)

**Do not run this in Manual mode.**

1. User explicitly requests N variants  
2. `generate keyframe --variants N --seed … --json` (or asset loop with spread seeds)  
3. Copy each output to previews: `gallery --src … --name {group}-{variant}.png`  
4. `gallery --project NAME --open` — user compares in browser (additive tiles; same tab refreshes on rebuild)  
5. **Wait for user to pick** — agent does not pick winner  
6. Only after pick: `select keyframe --image USER_CHOICE` or pin `reference_image` for assets  
7. Remind user to reload Gradio if open  

Never skip step 3–5 by picking a winner yourself.

---

## Mapping feedback to JSON layers

Use [prompt-writing-guide.md](prompt-writing-guide.md) scope rules:

| User says | Likely layer |
|-----------|----------------|
| Wrong clothes, face, body type | `characters[].prompt` |
| Wrong pose or framing | `keyframe.layout` or `--layout-override` on asset tests |
| Wrong environment details | `settings[].prompt` or `sequence.setting_prompt` |
| Wrong look/grade/lens | `styles[].prompt` or `project.style_prompt` |
| Wrong motion | `video.inbetween_prompt` |
| Wrong timing feel (multi-clip) | `sequence.action_prompt` |

After patch: propose save + **one** regenerate — wait for user OK.

---

## Video QC

Video only after user asked for video and approved keyframe stills.

### Frame-based review (when MP4 cannot be ingested whole)

1. Extract samples: `python {project-name}-files/scripts/video_qc_frames.py --phase2` (or `--seq seq14 --vid vid51`) — see [cost-and-context-guide.md](cost-and-context-guide.md) for the frame-count/resolution budget this script should respect. **The underlying mechanism** (so a fresh project's script can reproduce this without finding another project's copy first):
   - `ffprobe` to get clip duration
   - `ffmpeg` frame grabs at **0/25/50/75/100%** of duration always, plus denser sampling at **82/90/97%** for crash/impact beats (where the failure window is narrow and late in the clip)
   - each extracted frame scaled to **≤1024px** on the long edge before the agent reads it (story/pose/continuity QC does not need native resolution — see [cost-and-context-guide.md](cost-and-context-guide.md) § Image sizing for vision QC)
2. Output: `{project-name}-files/video-qc/{seq}_{vid}/` — `manifest.json`, `kf_start.png`, `kf_end.png`, `frames/t*.png`
3. Agent reads manifest **story_intent** + bracketing KFs + frames (open/mid/crash-window/end)
4. Score against storyboard intention and phase-2 anti-patterns (not prompt text alone)
5. Log verdicts in `{project-name}-files/video-qc-report.md`
6. Patch + clear `selected_video_path` on fails → scoped forward-pipeline regen (`--only`) → re-extract → loop until pass

### Judging a take across frames (not frame-by-frame)

Sampled frames each looking fine **in isolation is not a pass.** Three checks operate on the sequence as a whole:

- **Motion progression** — does the prompted action accumulate *gradually* across frames? A near-static hold for most of the clip followed by an abrupt, unmotivated change near the end is a fail in itself, even when every sampled frame is individually clean. (Distinct from anchor-based spatial consistency above: that catches things that wrongly *move*; this catches things that wrongly *fail to change smoothly*.)
- **Defect span outweighs the final frame** — a defect visible for any substantial span (e.g. the middle 50–75%) disqualifies the take regardless of whether it clears by the last frame. Viewers see the whole clip; do not weight the final frame more heavily. "It clears up by the end" is not an accepted standard.
- **Re-verify the full constraint list on every regen** — fixing the one named defect that motivated a regenerate does not pass the new take. Re-check *every* explicit prompt constraint independently against the new frames (framing, in-frame persistence, product legibility, expression, motion direction) — confirming one constraint holds is no evidence another still does.

**Solo-beat trap:** Never use “every dinosaur in both keyframes visible” when end KF already introduces the join partner — use explicit solo roster for that clip only.

For user viewing: **`gallery --src … --name … --open`** after agent approves. Same verdict format when asked.

---

## Explicitly out of scope

- `scripts/qc/run_qc_batch.py`, JoyCaption, `handle_auto_generate_with_qc` pose QC  
- Automatic pass/fail without user viewing the output  
- Autonomous multi-shot or keyframe→video pipelines  
- Modifying Gradio QC UI
