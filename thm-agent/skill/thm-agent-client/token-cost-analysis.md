# Token cost analysis — settleman-law-ad session

Staging doc, not canonical guidance. No exact token-counter tool was
available — these are principled estimates from known vision-token scaling,
actual operation counts from this session, and the size of text actually
produced/received. Order-of-magnitude, not measured fact. Relevant to
[cost-and-context-guide.md](cost-and-context-guide.md), which already covers
subagent delegation, long-run log hygiene, and image sizing for vision QC —
this session's findings extend that with concrete numbers from real use.

---

## Methodology

- **Vision tokens:** scale roughly with pixel count (~width x height / 750).
  Keyframe stills at the project's ~1152x768 default ~= **1,150-1,250 tokens
  each**. QC video frames (capped at 1024px long edge per the existing
  cost-and-context-guide.md guidance) ~= **900-1,000 tokens each**.
- **Text tokens:** estimated from actual tool-output length and response
  length observed this session.
- **Counts:** reconstructed from what actually ran in this conversation.

## Breakdown by category

| Category | Count this session | Est. cost/unit | Est. subtotal |
|---|---|---|---|
| Vision QC - stills (keyframe Reads) | ~14 reads | ~1,200 tok | ~17,000 tok |
| Vision QC - video (frame extraction + Reads) | ~48 frame reads across 9 video-QC passes | ~950 tok | ~46,000 tok |
| Generate calls (keyframe/video/asset; verbose debug logs in every JSON response) | ~22 calls | ~500 tok | ~11,000 tok |
| Gallery/select/discover CLI calls | ~40 calls | ~150 tok | ~6,000 tok |
| Research/exploration (builder.py, src/helpers.py, ugc/dino projects) | ~20 reads/greps | ~900 tok avg | ~18,000 tok |
| Initial skill load (SKILL.md + 7 guides, one-time) | 1x | - | ~20,000 tok |
| Memory + improvement-notes writes | 6 files | ~1,350 tok avg | ~8,000 tok |
| Agent response text (cumulative across ~90 turns) | - | - | ~20,000 tok (rough) |
| Granular-permission round trips ("shall I generate X" -> "yes") | ~20 exchanges | small each | low per-trip, but turn-count itself has overhead |

**Total session, rough order of magnitude: 140,000-160,000 tokens.**

## What actually dominates

**Vision QC is the single biggest line item — and video QC specifically
(~46K) outweighs still QC (~17K) by ~2.7x.** That's structural: every still
costs one read, every video-QC pass costs 5-7 reads. Across 9 video-QC
passes this session, that's the largest cost center by a wide margin.

**Two concrete instances of waste, not just estimates:**

1. **Re-reading the same still 3 times** during the Shot 4 "floating feet"
   exchange — each pass cost a full image read for a question that ended up
   resolved by the user pointing at the answer directly, not by the agent
   looking harder.
2. **CLI output verbosity** — `generate`/`gallery` calls print full debug
   dumps (`[REF]`, `[DEBUG]`, the entire assembled prompt restated,
   `Loaded configuration from config.toml` repeated 1-2x *per call*) into
   every tool result. Across ~40+ CLI invocations this session, that's
   repeated boilerplate landing in context every time, not signal.

**Lower-cost, often overlooked:** the initial skill load (~20K, one-time,
not really avoidable) and memory/notes writes (~8K, cheap relative to
everything else — not a place to optimize).

## Recommendations

- **Adaptive frame sampling, not a fixed 5-7 every time.** Fewer frames (3-4)
  for routine/low-risk QC passes; reserve denser sampling for contested
  shots or known-risky motion (rotation, depth-traversal).
  **Target doc:** cost-and-context-guide.md "Image sizing for vision QC" —
  add frame-count guidance conditioned on risk/contestedness, not just a flat
  ceiling.
- **Don't re-read on uncertainty — ask instead.** The 3x re-read on Shot 4
  cost more than asking "what specifically should I check" the first time
  would have.
  **Target doc:** vision-qc-guide.md — add a note alongside the existing
  "agent vision is advisory" framing: prefer asking what to look for over
  repeated re-reads of the same image when a verdict is uncertain.
- **CLI output is the most fixable structural cost.** If `generate`/
  `gallery` supported a `--quiet` flag (success path + file path only, full
  debug log suppressed unless something fails), that's a real reduction
  across every generation in a session with zero loss of QC capability.
  This is a tooling change, not a prompting one — out of scope for the skill
  docs themselves, but worth flagging as a CLI feature request.
- **Batch independent QC reads in one turn** rather than one-at-a-time
  across separate turns (already done in places this session, e.g. reading
  all 4 keyframes in one batch) — each turn re-processes context even with
  caching helping at the margins.
  **Target doc:** cost-and-context-guide.md — already covers subagent
  delegation for *batch* QC; this is the smaller-scale version of the same
  principle (batch the tool calls within a single turn) worth stating
  explicitly for single-agent, non-delegated work too.

---

## Suggested next step

Same pattern as the other two notes files: each recommendation is scoped to
a named target doc (mostly cost-and-context-guide.md and vision-qc-guide.md
here) and should be reviewed individually before merging. The CLI-quiet-flag
recommendation is the one item here that isn't a doc change at all — it is
a product/tooling suggestion for `cli.py` itself.
