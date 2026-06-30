# THM agent — anti-patterns

Common mistakes from real sessions. Link from [SKILL.md](SKILL.md) hard-stops when in doubt.

---

## Wrong → right

| Wrong | Right |
|-------|-------|
| Auto-generate all keyframes, then auto-`select keyframe` | One generate → show user → wait → next only when directed |
| Borrow creator/location/product from another sample project | Build fresh in **this** project's JSON and output folders |
| Seeds 1000, 1001, 1002 for "variety" | Spread seeds (10k steps): 10_000, 20_000, 30_000 |
| Jump to keyframes/video during asset phase | Respect *"assets only"* — creator → product → location first |
| Approval ask: `python thm-agent/cli.py generate keyframe ...` | Story beat: *"Shot 1 — store exterior establishing still — shall I generate?"* |
| Assume user sees chat Read or an IDE preview pane | `gallery --src … --name … --open` after every generate |
| Paste image path, URL, or markdown link in chat | **Never** — open HTML gallery; links are not user QC |
| Use `open` / `reveal` without user asking | Default is **gallery --open**; direct open only on request |
| Use an IDE's embedded/preview browser for QC | External browser via HTML gallery (`gallery --open`) |
| `gallery --clear` or delete preview tiles | Galleries are **additive** — only the user removes assets from `{workspace_root}/{project}-files/previews/` |
| Re-ask *"which project?"* when you created the JSON | Stay bound to `samples/{slug}.json` for life of agent |
| Put hero product in `styles[]` | Product in `characters[]` with asset ID + gallery |
| Agent picks variant winner without user seeing all options | Build gallery, open browser, wait for user pick |
| Run unattended generate → QC → patch → video pipeline | Manual gates between every step unless user opts up |
| `discover projects` or list samples to pick a file | One project per agent; new project → new agent |
| Assume *"straight"* means down-the-aisle | Confirm: often means **straight-on shelf** (camera facing products) |
| Per-project helper scripts as required workflow | Document recipes in skills; scripts are optional one-offs |
| Project scripts dropped in `thm-agent/scripts/` or `thm-agent/` root | Keep them in `{project-name}-files/scripts/` |
| Hardcoding `"samples"`/`"projects"` or a fixed `parents[N]` depth in a new project script | Derive `PROJECT` and `WORKSPACE_ROOT` from `__file__`, repo root from `cwd` — see generation-guide.md |
| Overwrite in-use `gallery_N.png` with a new test | Save as **new** gallery file; bind new path in JSON |
| Continue generating after user interrupt | **Interrupt = hard stop** — do not loop in same turn |
| Fixed N-iteration loops ("up to 5 tries") | Iterate only on clear failure or weak success; show each output |
| Infer colloquial "go again" as structured workflow reset | Treat as normal speech; clarify intent if scope unclear |
| Duplicate person, phantom limb, extra arms from frame edge | Single-axis fix; pose ref match-don't-rephrase; anatomy negatives |
| Assume standard four-slot ref setup (creator/location/product/pose) | Compare layout `imageN` to **active bindings only** — do not assign roles |
| Kitchen-sink prompt edits (many axes at once) | **One axis per attempt**; document what changed |
| Continue after user approves output or method | **Stop** when good enough; reuse approved **method** on later beats, still show each new output |
| File-size or largest-file variant pick | **Forbidden** — vision QC only; never `pick_best_variant` by bytes |
| Babysit multi-hour job in one agent shell turn | Detach subprocess; tail `pipeline.log` |
| System `python` for CLI | Use `[agent].python` from `config.toml` / `the-machine-ui-venv` |
| Host agent wipes sequences for "new story" | **New agent** + `clone-from-host --approve`; host JSON untouched |
| Claim vision QC when heuristic ran | Log `qc_method: vision` only after agent Read |
| Put style-layer LoRA in every `inbetween_prompt` | Style LoRAs on style asset; effect LoRAs on the one clip that needs them |
| Pin prior keyframe plate on travel/sprint KF2 | Setting asset + fresh full layout per KF |
| Movement LoRA in `inbetween_prompt` | Movement LoRA on style asset only; sprint motion in words on video |
| Crash LoRA on style + crash video | Effect LoRA on **one** impact clip only |
| Pre-impact commit pose on KF when video carries crash | KF = post-impact or outcome state; intact obstacle in video, hit in final 20–30% |
| Corner beat reads as U-turn | Peel onto perpendicular street; prior path behind snouts; barrier ahead on new axis |
| Vague inbetween ("they run through scene") | Lane + snout direction + per-entity obstacle target every clip |
| Revert user's JSON edits after `load_project` | Ask; user/Gradio edits are ground truth |
| Empty crowd text when principals move fast | Directed independent background business + targeted negatives |
| 2 KF + open end only when user asked for 3 videos | Pick a `video_plan` shape that yields 3 gaps ([schema-reference.md](schema-reference.md)) |
| Regen corrected keyframe only; leave bracketing videos selected | Regen **idN** → clear **both** adjacent videos (inbound + outbound); same `inbetween_prompt` if user gave no notes on that clip |
| Re-run completed sequences after crash | Resume forward from first incomplete beat |
| Many separate CLI invocations for one arc | One detached forward script + skip-complete |
| Interrupt without killing detached runner | Terminate pipeline processes; verify none remain before same-turn resume |

---

## Session discipline (Manual QC)

Distilled from long Manual QC runs — process rules, not project-specific recipes.

### Human-in-the-loop

- **Default: Manual, gate-driven** — one generation, show via `open` / `reveal --select` / `gallery --open`, wait.
- **User is ground truth; agent vision is advisory.** Do not treat agent Read of `workspace_path` as user approval.
- When the user says output is **good enough** or picks a variant → **stop**. Do not reopen that beat unless they ask.
- **Interrupt = hard stop.** Do not continue loops or regens in the same turn.

### Approved method reuse

When the user approves a **prompt pattern**, binding setup, `video_plan` shape, or iteration strategy, reuse that **method** on later beats in the same session — still **show each new output** until trust supports higher automation. Approval of the approach is not permission to skip showing results. See [SKILL.md](SKILL.md) § Approved outputs and methods.

### Iteration discipline

- **One axis per attempt** — stacking fixes often causes regressions elsewhere.
- Continue without a new user gate only on **clear failure** or **weak success** (specific deltas noted).
- No fixed-count loops — each attempt gets shown unless user opted into Assisted/Semi-auto.
- Do not infer ritual phrases (e.g. colloquial "go again") as a structured reset — clarify if scope is unclear.

### Reference bindings

- Layout **`imageN` descriptors must match what is actually bound** — no assumed slot roster.
- Reload JSON from disk before generate; scan layout for `imageN`; drop or rephrase text for unset refs.
- **Do not assign or assume** what the user will provide (creator, location, product, pose). Beats vary.

### JSON reliability

- **`load_project` before every edit** — file can diverge from Gradio saves or user edits.
- **User / Gradio JSON is ground truth** — if prompts, LoRA strengths, bindings, negatives, or selections differ from what you last wrote, treat user edits as intentional. Do not revert or normalize unless asked. If inconsistent, ask.
- Call **`_refresh_video_chain`** when changing `video_plan` — avoid orphan video entries.
- **Spread seeds** for variety; consecutive seeds → near-duplicates.

### LoRA and crowd (prompt layers)

- **Style LoRAs** on style asset; **effect LoRAs** on the one video clip that needs them — not duplicated across every inbetween.
- Do not mirror style motion bias with vague crowd text — crowds will mirror gait/style LoRAs.
- Principals moving fast → background humans need directed independent business, not blank crowd fields.

See also: [prompt-writing-guide.md](prompt-writing-guide.md) (iteration, profile lock, `video_plan`), [asset-library-guide.md](asset-library-guide.md) (additive galleries), [SKILL.md](SKILL.md) (automation path).

---

## Trust reset: auto-select

If you auto-selected keyframes or advanced to video without user approval:

1. Acknowledge the violation
2. Clear or revert `selected_image_path` if user asks
3. Return to Manual: one gen, show output, wait

Do not defend batch automation as "efficiency" — default is human-in-the-loop.

---

## Visibility

Chat and IDE preview panes often **do not** show generated images to the user.

After **every** generate, open the **HTML preview gallery** (preferred — never paste links):

```bash
python thm-agent/cli.py gallery --project PROJECT_NAME \
  --src WORKSPACE_PATH --name keyframe-shot1.png --open
```

Only if the user **explicitly asks**:

```bash
python thm-agent/cli.py open WORKSPACE_PATH
python thm-agent/cli.py reveal WORKSPACE_PATH --select
```

Agent `Read` of `workspace_path` is **agent-only** QC — not a substitute for `gallery --open`.

---

## Scope

| Phase | Allowed | Forbidden |
|-------|---------|-----------|
| Asset | `generate asset`, pin `reference_image`, preview gallery | `generate keyframe`, `generate video`, storyboard table edits for shots |
| Keyframe | `generate keyframe`, `select keyframe` after approval | Video without approved stills |
| Video | `generate video` after user asks | Same-turn keyframe + video |

---

## CLI dumps in conversation

CLI and JSON are **agent-internal**. The user approves **story beats**, not shell commands.

Exception: user explicitly asks *"what command did you run?"* or *"show me the layout field"*.
