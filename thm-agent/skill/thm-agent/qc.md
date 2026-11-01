# QC

How the agent judges a take and what it writes down. QC is the only thing
that turns a render into a decision; `history --bin <id>` shows the next
session nothing but the marks and notes you leave here.

## What QC is

- QC is **agent vision on thumbnails**. You fetch the take with `thumb`, look
  at it, decide, and `mark_take` it with `pass`, `fail`, or `in_progress` and
  a note. Nothing else counts as QC.
- **Never file size.** Byte count, resolution, mtime, or any other non-vision
  proxy is not a verdict. A bigger file is not a better take.
- **A mechanical sweep is not QC.** Duration-vs-length_s, a present audio
  stream, a playable container: those prove the file exists and plays. They
  never make a take pass. A sweep reported as QC is a lie; a real sweep with
  "0 flags" has missed oversized props, wrong actions, and forbidden faces.
- **A user comment on one axis resolves one axis.** "The slow-mo looks good"
  clears motion speed and nothing else. Identity, hands, count, wardrobe,
  background, and text are still open until you have looked at them. A scoped
  remark is not a pass.
- A pass is a claim about every axis at once. If you have not checked an
  axis, the take is `in_progress` with the unchecked axis named in the note.

## Sizes

`thumb <media> --out f.webp --size grid|detail|poster|file`

| size | use it for |
|---|---|
| `grid` | triage: which takes are worth a closer look, obvious count and framing failures, ruling a take out |
| `detail` | the verdict: identity, hands, wardrobe, text, background anchors; every pass comes from a `detail` read |
| `poster` | the user-facing frame when you need to describe what they will see; rarely needed for a verdict |
| `file` | only when resolution itself is the question (fine text, a label, a hand at small scale you will crop and upscale) |

Video: `frames <media> --out sheet.webp --n 4` gives a contact sheet. Four
frames is the default and covers most clips. Go to 6 or 8 (`--n 8` is the cap)
for multi-subject clips, clips whose important beat lands late, and anything
already contested. Mid frames are where defects hide: the first frame is the
start reference and the last frame converges to any end reference, so a clean
first and last frame proves nothing about the middle.

Do not raise frame count by habit. Four for routine, eight for risk.

## Method

1. **Compare against the bound references, not the plan.** `bin <id>` lists
   what is bound; the bound plate is ground truth for identity, garment,
   location, and scale. A plan, a brief, or your own memory of what was bound
   is a snapshot that went stale the moment the user rebound something. When
   a take disagrees with your expectation, check the binding before you call
   it drift. Judging a render against a plan while the user had rebound the
   outfit has produced a false "drift" report on a take that matched its plate
   exactly.
2. **Anchor-based background consistency.** Pick one fixed element visible
   across the frames (a light panel, a doorway, a horizon line) and check that
   everything else keeps its spatial relationship to it. Separately track one
   object's size and shape through the frames. Judging each frame alone misses
   furniture that relocates and props that grow.
3. **Count heads, then tick every identity once.** On any group frame: count
   the people first and compare to the intended number; then tick each named
   identity present exactly once; then scan the background for a clone. A
   group take that "looks dynamic" with one dancer duplicated and one missing
   is a fail. Do this before any other axis.
4. **Judge the clip, not the frames.** Motion has to accumulate across the
   clip. Near-static for most of the clip then a jump at the end is a fail
   even when every sampled frame is individually clean. A defect that spans
   the middle of the clip fails the take even if the last frame is clean;
   viewers watch the whole clip, and "it clears up by the end" is not a
   standard.
5. **A still verdict can invert in motion.** A composition that is marginal
   on the still (a subject near the frame edge, a prop that could crowd the
   subject once they move) may hold or may not once the video model gets it.
   Do not resolve that from the still; flag it as "your eye in motion
   decides" and let the user or the clip settle it.
6. **Re-verify the full constraint list on every regen.** Fixing the one
   defect that motivated the reroll does not pass the new take. Check every
   explicit constraint again, independently: framing, in-frame persistence,
   count, wardrobe, expression, motion direction. One constraint holding is
   no evidence another still does.
7. **Canon comes from the user-named reference, never the majority of recent
   takes.** When the user points at a take ("like take 3"), diff every
   attribute against that image. Majority-vote consistency amplifies your own
   earlier mistake and then propagates it.
8. **Audit plates like renders.** A reference with a missing arm or a wrong
   garment propagates into every bin that binds it. Look at the plate.
9. **Small subjects: crop and upscale.** At `grid` size a person in a wide
   frame is a few dozen pixels. Use `file`, crop each subject, upscale, and
   check head, torso, legs, and shoes against the plate per subject.
10. **Check the start reference before blaming the video model.** A clip that
    carries a defect is often faithfully following a still that already had
    it. Diff the still first; a still-level defect is fixed in the still's bin.
11. **Geography and anatomy before faces, on every keyframe and every
    sampled frame.** In this order: which side of the set's line each person
    is on (a bar counter is the line: the camera on the bartender's side, the
    customers behind the counter, nobody on the camera's side); left/right
    against the established line; heads; hands per person (two each, count
    them in every frame, not once); props that must be single (one phone, one
    basket). A diagonal or off-axis angle is where "which side of the counter"
    goes wrong silently: find the near edge of the counter and check the
    person is behind it. On online-gaming the user caught a person on the
    wrong side of the bar, a third hand that was already in the keyframe, and
    an extra stranger, all after my pass; each was visible in the still.
12. **Continuity against the established takes, item by item.** Wardrobe
    pieces (long sleeves, cap), accessories that must not appear (a watch),
    prop variants (whose phone has which case), what is on the bar. A new
    keyframe that "looks right" grew a wristwatch and swapped Dev's red phone
    case for black.

## Failure modes

Look for each of these on every take. Name the one you found in the note.

- **Identity drift or swap**: the face, hair, or build wanders from the bound
  plate; in a group, two people trade features. Crossing, airborne, and
  occluding beats are the worst swap risk; compare each subject at takeoff
  and landing, and check subject order across the clip.
- **Scale**: a prop or a person at the wrong size relative to the scene. Scale
  comes from the bound plate's apparent size, not from words.
- **Hands and anatomy**: finger count, phantom limbs, extra arms from the
  frame edge, a joint bending wrong.
- **Morphing**: an object changing shape or kind mid-clip; a sealed box that
  opens; one straw that becomes three.
- **Static or motion progression**: minimal motion, motion that starts late
  and crams into the last frames, an internal dissolve or cut.
- **Background rigidity**: fixed features that move, furniture that relocates,
  parallax that breaks during depth travel.
- **Count and cloning**: extra figures behind the principals, a duplicated
  subject, a missing one.
- **Text**: hallucinated lettering, garbled labels, a wrong product name.
- **Off-frame wardrobe migration**: a garment named in the prompt but out of
  frame on the plate gets pulled into frame or onto the wrong person. Leave
  out-of-frame garments undescribed.
- **Set additions**: something the set never had appears mid-clip (a TV on
  a wall, a colored wash flooding the frame). Compare the walls and the light
  in the last frame to the first.
- **Impossible props**: a phone whose back shows a screen or video, a second
  phone, food that changes basket. A phone has one screen, on its front.
- **Beat reversal**: the action resolves the opposite way (the hand that
  should be stopped escapes with the wing). The beat is an axis; a clean clip
  that tells the wrong joke fails.
- **Stale output**: a done generation whose take is identical to the previous
  take. A reroll that changed nothing is a failed generation, not a pass;
  check the seed in `history` and say so in the note.

## Audio QC

You cannot hear, but you can measure, and the note says what was measured. The
`audio` tool (`tools`, read `thm-tools/audio/GUIDE.md` once) replaces the old
"audio not checked (I cannot hear)" line. On a dialogue take, in this order:

1. **The words.** `tool audio transcribe <media> --expect "<the line>"`, with the
   line taken from the take's own recipe (`recipe <media>`), never from the bin's
   current prompt. `wer == 0` is "line correct"; a contraction or filler difference
   under 0.15 is still the line, say so. A run of `extra` words is the model
   speaking direction text or gibberish; `missing` words are a dropped line. A
   `low_confidence` word that "matches" is not proof: run `--model medium` as the
   second opinion before failing or passing on the words alone.
2. **The voice.** `tool audio voice <media> --ref <the character's reference take>
   --window s0:s1` with the line's `start_s:end_s` from step 1 (a two-shot has both
   voices in it). The reference is the take the user approved for that character,
   spoken, not shouted, recorded as a project rule (`add_project_rule
   kind=continuity "Tommy's voice: m_5ee6436e"`) the first time. Same voice >= 0.75;
   another render of the character 0.50-0.75 (the drift the user hears as an
   inconsistent accent or timbre: report the number, compare the bin's takes
   against each other); another voice < 0.45. The margin to the other character
   names the speaker: the words can be right and the speaker wrong.
3. **The stress**, when the prompt marks one in CAPS (`filmmaking.md`):
   `tool audio emphasis <media> --expect "you know what YOU can do"`. Trust it only
   on a take whose words matched in step 1. `stress_matches` with a margin over
   0.8 is clear; under 0.5 say "stress shared between X and Y".
4. **The levels.** `tool audio levels <media>`: clicks and pops with their times,
   stray bursts at the head or tail, a hot peak. No flags and a voice P90 in the
   project's usual band is "levels fine".

The note states what was measured and the correction, in one line each:

```
audio:   line correct (WER 0); voice matches Tommy 0.81 (Dev 0.42); stress landed on
         "what", not "YOU" (margin 0.9): re-render with the stress cue moved; levels fine
```

What remains a **please listen** judgment, named as such in the note: sarcasm,
warmth, naturalness, whether the pause is right, lip sync, and whether the
accent is the one the user wants. A take whose words, voice and stress measure
right and whose read you cannot vouch for is `in_progress` with "please listen:
delivery" open, not a pass. A clip with no dialogue still gets `levels`.

## Honest limits

- You cannot hear. The audio tool tells you the words, the speaker, the stressed
  word and the levels (above); it cannot tell you whether the read is good or
  the lips match. On any audio-bearing clip the note carries the measurements
  and names what is left for the user's ear. Do not auto-pass a clip whose
  primary output is dialogue on picture alone.
- Do not re-read the same image on uncertainty. A second and third read of
  one still has never resolved a question the user could answer in one line.
  Ask what to look for instead.
- A verdict you are unsure of is `in_progress` with the doubt named, not a
  pass with a hedge.

## The note

`mark_take --note` is what `history --bin <id>` shows beside the take. Write
it for the next session, which will have the note and the thumbnail and
nothing else. Template, one line each:

```
changed: <what changed since the previous take: prompt clause, seed, reference, framing>
fixed:   <what that change repaired>
broke:   <what it regressed, or none>
open:    <what is still wrong or unverified, including "please listen" items>
audio:   <words / voice / stress / levels as measured, and the correction, when the take has sound>
```

The first line is the important one. History diffs the prompt for you, but it
does not know why you changed it or what you were trying to fix. A note that
says only "hands better" tells the next session nothing about the seed, the
reference, or the still-open count problem.

## Cost rules

- **Batch reads in one turn.** Fetch every thumbnail you intend to judge, then
  judge them, rather than one read per turn.
- **Verdicts, not images, from subagents.** When a batch is big enough to
  delegate, the subagent reads the thumbnails and returns status plus note
  text; the parent never holds the images. Delegate a run of bins, not one
  image.
- **Log each verdict immediately.** `mark_take` as soon as you decide. Do not
  accumulate verdicts in conversation and write them at the end.
- **Do not hold a run's images open to compare take 1 against take 40.** Judge
  each take against its bound references and its own history entry, mark it,
  and let the image fall out of context. `history --bin <id>` is the
  comparison surface; the images are not.
- Use `grid` for triage and reach for `detail` only on the takes that survive
  it. Reach for `file` only when resolution is the question.
