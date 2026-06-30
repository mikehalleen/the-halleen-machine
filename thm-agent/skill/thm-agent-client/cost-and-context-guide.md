# THM agent — cost & context discipline

How to keep token spend and context size under control on long or batch-heavy
work: when to delegate to a sub-agent, how to handle logs on multi-hour runs,
and how to size images for vision QC. None of this changes the approval gates
in [SKILL.md](SKILL.md) — it changes *how cheaply* the agent does the work
those gates already allow.

---

## Subagent delegation

Reviewing many generated outputs in one pass — Unattended Phase 1 vision QC
across several beats, or a multi-beat batch regen — can pull dozens of
full-resolution images into the orchestrating agent's context if done inline.

| Rule | Detail |
|------|--------|
| **Delegate batch QC, not single images** | One sub-agent/task call per ~5–10 beats (or per sequence), not one per image — delegation overhead isn't worth it below that |
| **Return verdicts, not images** | The sub-agent reads the images and reports back **verdict + rationale text only** (`Pass` / `Revise` / `Reject` + the deltas) — the parent agent never needs to hold the raw images itself |
| **Same gates apply** | A sub-agent doing QC still follows every rule in [vision-qc-guide.md](vision-qc-guide.md) — no file-size heuristics, no auto-select, plain-language reporting upstream. Delegation changes **where** the work happens, not the rules it follows |
| **Log immediately** | Have the sub-agent (or the parent, on its behalf) call `pipeline record-qc --rationale` per beat as it goes — don't accumulate verdicts in conversation and write them all at the end |

This applies to Unattended/Continuous-forward batch work — not to normal
Manual-mode single-beat QC, where there's only ever one image in play and
delegation would be pure overhead.

---

## Long-run context hygiene

Multi-hour jobs (see [generation-guide.md](generation-guide.md) § Long-run
playbook) generate far more log and checkpoint data than fits — or is useful
— in context at once.

| Situation | Do this | Not this |
|-----------|---------|----------|
| Checking progress mid-run | Tail the last ~50–100 lines: `tail -n 100 thm-agent/workspace/{project}/pipeline.log` (PowerShell: `Get-Content -Tail 100`) | Reading the entire `pipeline.log` from the start |
| Confirming generation state | `comfy-status --json` + `pipeline-checkpoint.json` | Re-reading prior turns' full CLI output to reconstruct state |
| Resuming after a crash or interrupt | Read the checkpoint, the last log tail, and the JSON for the **first incomplete beat only** | Re-reading the whole project JSON or full run history to "catch up" |
| Reporting status to the user | Summarize in plain language (beats done / remaining, any flagged failures) | Pasting raw log lines or full JSON into chat |

The goal is the same skip-complete discipline already required for the
pipeline itself (don't redo finished beats) applied to the agent's own
context (don't re-read finished history).

---

## Image sizing for vision QC

Vision QC judges story/pose/anatomy/intent match — not pixel-level fidelity.
Reading images at native generation resolution for that purpose spends tokens
the judgment doesn't need.

| Surface | Current behavior | Guidance |
|---------|-------------------|----------|
| `video_qc_frames.py` → `extract_frame()` | ffmpeg extracts each sampled frame at the source video's native resolution, no scaling | Add `-vf "scale='min(1024,iw)':-2"` (cap long edge at 1024px) to the ffmpeg call — story/pose/continuity reads don't need full native res |
| `video_qc_frames.py` → `sample_times()` | Already capped at up to 8 frames per video QC pass (4 base + 3 crash-beat + 1 end) | Treat this as the intentional ceiling — don't quietly raise frame count in a new script without weighing the added cost per video |
| `mirror_generation_output` (keyframe-still copies) | Copies at full native resolution | **Keep user-facing copies full-res** — they're what the user sees via `gallery --open`. Agent-only QC reads can tolerate a smaller copy; don't downsize the file the user will actually view |
| Any new QC-frame-extraction script | — | Follow the same `-vf scale=` pattern as `video_qc_frames.py`, whether it's for the dino arc or a future project |

**Don't hold a whole run's images "open" to compare across beats.** Score each
beat against its own storyboard intent as you go, log the verdict via
`pipeline record-qc --rationale` immediately, and let that image's tokens
fall out of context — there's rarely a reason to compare beat 1 against
beat 40 directly.

---

## Explicitly out of scope

- Changing what the **user** sees in `gallery --open` — galleries stay
  full-resolution; this guide only governs agent-only QC reads
- Any automated pass/fail decision based on file size, resolution, or other
  non-vision heuristics — forbidden regardless of cost (see
  [vision-qc-guide.md](vision-qc-guide.md))
- Replacing the approval gates in [SKILL.md](SKILL.md) with batch automation
  — cost discipline applies **within** an already-approved automation level,
  it doesn't grant a higher one
