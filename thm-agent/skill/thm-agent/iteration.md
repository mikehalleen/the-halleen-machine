# Iteration

How to get from a first take to a take the user picks, spending as few
renders as possible. Every attempt is one change, and every change is
recorded in the take's note so `history` can show it.

## The ladder

Prove a direction cheap, hold it, then spend on it. The user set this order
on van-halen and it stands as the default for every project:

1. **Quarter and short.** `generate --bin <id> --params '{"quarter": true, "length_s": 3}'`
   for video; for stills, a small canvas in `width`/`height`. The question at
   this rung is only "is this the right direction": composition, action,
   identity in the rough. Spread seeds here; three quarter renders cost less
   than one full one.
2. **Longer, still quarter.** Once a direction holds, lengthen (`length_s`
   toward the bin's setting) at quarter size to see whether the motion holds
   over the whole clip, or whether it goes static then jumps.
3. **Full size.** Drop `quarter`, drop the `width`/`height` overrides, keep
   the seed that held, and render the candidate. QC the full-size take
   against the full constraint list again; a quarter pass is not a full pass.

Do not skip rungs on a new look. Skip to full size only when a bin is already
holding and you are varying one clause on a known-good seed.

## One axis per attempt

Change one thing between takes: one prompt clause, one reference, the seed, or
the framing. Never several. Stacking fixes causes regressions you cannot
attribute, and `history`'s prompt diff becomes unreadable. The note's first
line names the one axis; if you cannot write that line in a few words, you
changed too much.

## Reseed vs reprompt vs rebind vs reframe

Decide from the kind of failure, not from how many takes you have made.

- **Direction or choreography failure** (the action went the wrong way, the
  camera drifted, the composition is fine but this roll is not): **reseed
  once.** Same prompt, same references.
- **The same defect survives two identical rerolls**: a third identical roll
  is not stochastic, it is **systematic**. Stop rerolling. Change the prompt
  clause that names the affected element (element, coverage, duration, and a
  negative for the exact failure you saw), or change the reference, or change
  the framing. Pick one.
- **Two failed prompt fixes on one defect**: the composition is the problem.
  **Reframe the composition** with fewer independent elements: a tighter insert, one
  subject instead of two, hands at the frame edge instead of bodies. Less in
  frame means less for the model to invent away. Reframing is a creative fix,
  not a compromise.
- **Identity failing with a clip bound**: a bound video reference outweighs
  the stills bound beside it. Demote it (move it later in `reorder_references`)
  or `unbind_reference` it and let the stills carry identity.
- **Scale wrong**: bind a **scale-anchored plate** (one that contains an
  object of known size beside the subject), and use comparative size language
  against things in the scene. Adjectives like "small" do not move scale;
  the plate's apparent size does.
- **A wrong start still cannot be rescued by the video prompt.** Fix the still's
  bin first, then regenerate the clip.
- **Persistence failures** (a box that opens, a prop that multiplies, a subject
  that leaves frame): one persistence clause in the prompt plus the failure
  mode in the negative. If that fails twice, reframe.

## Seeds

- Spread seeds: `10000`, `20000`, `30000`. Never consecutive; `1000`, `1001`,
  `1002` give near-duplicates.
- **Exploring a new identity or look**: no reference bound, spread seeds, pick
  from the spread. The winning take becomes the plate other bins bind.
- **Iterating on a bound identity**: keep the reference fixed. Vary the prompt
  and the seed independently, never both in one take, so you can tell which
  one moved the result.
- The seed is backfilled when you omit it and recorded on the generation, so
  every take is reproducible. `recipe <media>` prints it; `history --bin <id>`
  lists it per take. Reuse a known-good seed when you change one clause and
  want to see only the clause's effect.

## When to stop

- **The user's pick ends it.** When the user selects a take or says it is good
  enough, that bin is done. Do not reopen it unless asked.
- Continue without a new gate only on **clear failure** (a named defect) or
  **weak success** (specific deltas you can name). "Could be better" is not a
  reason to render.
- **No fixed-count loops.** "Up to five tries" is not a plan; each attempt
  follows from the last take's note.
- **In auto mode** a bin is done when your own QC passes it against the goal
  as stated in the brief or the note, or when a cap is reached: **3 takes with
  the identical defect**, or **8 takes on one bin without a pass**. At the cap,
  post the candidates (take numbers and the open defect), mark the best
  `in_progress`, and stop. The user decides whether to reframe or move on.
- Do not chase a marginal composition with renders. Flag it as "your eye in
  motion decides" and let the user look.

## Regeneration dependencies

A clip is made from the references bound at the time of the take. When a
still that a clip uses as its start frame (reference position 1) or end frame
(position 2) gets a new selected take, every clip made from the old still is
stale even if it looked fine on its own. `recipe <media>` shows which media a
take was made from; regenerate the dependent clip with the same prompt unless
the user flagged that clip too. Say so in plain language: fixing the still
means redoing the clip that starts on it.

The same holds for any bound plate: rebinding a character or location plate
invalidates the takes that bound the old one, in every bin that referenced it.
`check --bin <id>` confirms the handles still resolve after a rebind.

## Use history before every reprompt

`history --bin <id>` before you touch the prompt: take number, seed, params,
QC status and note, and the prompt diff against the previous take. It tells
you which clauses have already been tried, which seeds have already rolled,
and what the last note left open. A reprompt that repeats a clause history
shows already failed is a wasted render. `recipe <media>` for the one take you
want to reproduce exactly; `recall_generation` puts the bin back to that
recipe when the user wants to branch from an earlier take.

## A held seed is visible, or it is a trap

Holding a seed while you change one prompt clause is the right way to isolate that clause,
and it makes takes look alike on purpose. The user watching the gallery cannot tell a held
seed from a stuck agent. So: every `generate` carries `--reason` saying what the one change
is and whether the seed is held ("same seed as take 2, one change: the pacing clause"); the
feed line and the QC note both say it; and after two holds on one seed, roll a fresh one
before the next prompt change so the user sees the recipe move. Never hold a seed across a
reference change, since the comparison is meaningless then.
