---
name: thm-performance
description: Direct the human performance in a THM image or video prompt and QC the result, using Milton Katselas's acting Checklist. Use when a prompt puts a person in a shot, when a take reads flat, posed or "AI-average", when the user asks for more natural or less generic performances, and on the first shot of a sequence or any shot after a cut in time or place (the moment before). Companion to the thm-agent skill; never needed for plates, props or locations.
---

# Performance direction for generated people

Image and video models render the statistical average human: feet parallel, weight
centred, arms symmetrical, hands at the sides, a catalogue smile, eyes to the lens, the
whole emotion present in frame one at full volume. Nothing is wrong and nothing is alive.
This skill is how you push a prompt off that average and how you tell, in QC, whether a
take got there. It is the performance half of `filmmaking.md`: that file decides the
shot, this one decides what the person in it is doing.

The source is Milton Katselas's **Checklist** (*Acting Class: Take a Seat*, Phoenix
Books 2008; taught at the Beverly Hills Playhouse): twenty tools an actor can use to
approach a scene. Katselas's own rule about it is the first rule here: **it is a menu,
not a form.** Any one tool can unlock a scene; nobody uses all twenty. Pick two or three
per shot. A prompt carrying ten performance notes gets none of them, because the model
averages them back out, and a frame where everyone fidgets is as fake as a frame where
nobody moves.

## The Checklist, translated

What each tool meant for an actor, and what it becomes in a prompt. "Still" means a
keyframe or portrait bin, "video" a clip bin.

| tool | for the actor | in the prompt | where |
|---|---|---|---|
| **Event** | what is actually happening, not the theme | name the concrete happening: `she finds the receipt in his coat`, never `a moment of betrayal`. The model can render a receipt; it cannot render betrayal | both |
| **Specifics** | make the details of the scene concrete | one non-default physical fact per person that a reader could sketch: `weight on the left hip, right foot turned out`, `left sleeve pushed to the elbow, right one down`, `sitting on the front edge of the chair, not the back`, `collar half turned under`, `one shoelace undone`. Never a pose label (`relaxed stance`) | both |
| **Arbitrary Choices** | an illogical choice that reveals the character through contradiction (Kazan) | one small, human, unexplained action that cuts against the mood: `checks a fingernail while he shouts`, `straightens the coaster while being told off`, `laughs once, at the wrong line`. **One per shot**, not one per person | both, mostly video |
| **Behavior** / **Use of Objects** | do something besides say the lines; connect through objects | give the hands a task that is not the dialogue and not a gesture: `turning the phone face down`, `working a zip that sticks`, `the cup held by the rim, not the handle`. Still: mid-grip. Video: the task in progress, finished or abandoned by the end | both |
| **Physical / Emotional State** | the body's condition, and how it shifts | a state the body shows before the face does: `cold, shoulders up, hands in sleeves`, `winded, still catching breath`, `two drinks in, blinking slowly`, `has not slept` | both |
| **Inner and Outer Life (The Cover)** | the surface behavior over the hidden feeling; never charm with nothing under it | write the surface and the leak, never the feeling: `smiling for the room, thumb working the ring`, `agreeing with him, eyes on the door`, not `sad`, not `anxious` | both |
| **Creative Hiding** | play part of the scene into an indirect focus | the character does part of the beat into a thing, not a person: `says it to the sink`, `answers while reading the glovebox`, `looks at the window for the hard line` | both |
| **What happened before the scene** | the moment before | see **The moment before** | both, decisive on video |
| **Moment to Moment: Alternatives** | explore the options the character does not take | a visible almost: `reaches for the glass, stops, takes the keys instead`, `starts to answer, does not, then does`; a breath before the line | video |
| **Humor** | humor and irony as a way of coping | the coping move, never a gag: `a short laugh at herself before she says it`, `he toasts the empty chair` | both |
| **Objectives** | want what the character wants, without caution | name the want from the other person, not the feeling: `trying to get him to look at her`, `trying to end the call without saying goodbye`. The want sets the lean, the eyes and the distance | both |
| **Be a Person** | what would a real person do here; real people have a reflective delay | the half-beat: people react late, not on cue; the imperfect gesture; the restraint rules below | video |
| **Trust**, **Being Personal**, **Pathology**, **Who's the Author**, **Evaluation**, **Moment to Moment: Belief** | the actor's inner work | **not prompt tools.** Nothing to write. Belief is QC's job (below). | - |
| **Improvisation** | play the scene in your own words | one use only: when a model garbles a scripted line, write the paraphrase of the line and let it speak that | video, dialogue |

## Emotion: play the fight, not the feeling

Real people try not to show what they feel; weak actors show it. Models are weak actors.
Every emotional beat is prompted as the attempt to hold it in, and the leak. Each row is a
prompt pattern and a QC criterion in one.

| the beat | prompt the fight | never |
|---|---|---|
| crying | `trying not to cry: jaw set, blinking hard, swallows, looks up and away, voice held level`; at most `one tear she does not wipe` | streaming tears, a crumpled face, sobbing with the mouth open |
| anger | `very still, voice dropped, puts the cup down with exact care` before any raised voice, if a raised voice comes at all | bared teeth, pointing, shouting from frame one |
| fear | `goes still, hands small and close to the body, breathing shallow, eyes tracking the sound` | a scream face, hands up by the cheeks, flinching on cue |
| joy | `fights the smile down, looks away, then lets it out` | the catalogue grin from frame one |
| surprise | `freezes first, half a second, then the gesture` | open-mouth gasp with raised hands |
| laughter | `the laugh breaks her posture: shoulders drop, she leans on the counter` | a posed open-mouth laugh, head thrown back, holding still |
| grief, relief | arrive late: `nothing at first, then the shoulders go` | the full state in the first frame |
| love, warmth | `watches him a beat longer than needed, then looks at the menu` | a held mutual gaze |

**One notch under.** Prompt the emotion a step quieter than the beat seems to need; the
model adds a step. If the prompt says "furious" the take screams; if it says "holding his
temper" the take is furious.

**Onset is the performance.** On video the feeling has to arrive during the clip. A take
that opens at the peak and holds it is a still with motion blur. Write the onset as beats:
`opens on X, then Y, ends on Z`.

## The moment before

A shot never starts in neutral. Whatever the character was doing a second before the first
frame is still in the body for the first half-second to second of the clip, and in a still
it is the pose. Apply this on:

- the first shot of a sequence;
- any shot after a change of place or a jump in time;
- any shot after a cut where the previous shot's activity would still be in the body (she
  was laughing in the last room; she walks into this one with the laugh fading);
- any single whose character has just been addressed or has just acted in the shot before.

Residue is physical, finished or finishing: `putting the phone away`, `still chewing`,
`shaking the rain off her sleeves`, `coat half off one shoulder`, `the laugh from the other
room fading as she sits`, `catching his breath from the stairs`, a weight shift, a breath.

- Video: `opens on her still <residue>, then <the beat>`. The residue takes the first beat
  of the clip and the event takes the rest.
- Still (a keyframe the clip will start from): the residue **is** the pose. A keyframe that
  shows a neutral stance will give you a clip that starts neutral; the start frame decides.
- A hot start (`filmmaking.md`) is a moment-before choice, not an exception to it: the
  action already underway is the residue.

Decide the moment before from the sequence, not the shot: read the previous item in
`sequence show`, and write down what state it leaves the character in.

## One character, many takes: the performance book

You own every take in the project, so consistency is yours and change is deliberate. The
user decides who these people are; you propose, then enforce.

**Define the vision, with the user, once per character.** Before the first shot list
(in plan mode, in the brief; otherwise as a `post` the user can correct), propose for each
principal:

- two or three **signature specifics**: how they stand, what the hands do when idle, the
  one arbitrary habit that is theirs (`Sally stands with her weight on one hip and her
  arms folded low; idle hands fold a straw wrapper; checks her nails when lied to`);
- the **cover**: what they show and what they hide (`Jim performs ease: smiles for
  everyone, and his eyes go to the exit when the money comes up`);
- the **tempo**: fast or slow, early or late reactor, talks over or waits (`Sally answers
  before he finishes; Jim waits a full beat`).

Record each as a project rule the moment the user agrees or corrects it, kind `continuity`,
in the user's words:

```
cmd add_project_rule --payload '{"kind": "continuity", "text": "Sally: weight on one hip, arms folded low; idle hands fold a straw wrapper; checks her nails when lied to. Cover: bright, and looks at the exit when money comes up. Tempo: answers early."}'
```

`rules` brings it back every session; read it before every shot card and every QC.

**Signatures repeat, the arbitrary choice does not.** Every prompt for a character carries
their signatures in the same words, the way wardrobe repeats across prompts in
`filmmaking.md`. The arbitrary choice and the object task vary shot to shot; the signatures
are what make the clips read as one person.

**Change is a story event.** When the arc moves (she stops hiding it; he finally sits
down), update the rule with the new state and the shot where it changed, state the new
state in every later prompt, and QC later takes against the new rule, not the old. A
character who changes without a reason you can name has drifted, and that is a QC fail.

**Contrast between characters.** Two people in a scene never share a tempo, an idle-hands
habit or a cover. If one is still, the other moves; if one reacts early, the other late; if
one performs ease, the other performs effort. Check the book for overlap before the first
two-shot.

**A user note on a performance axis is a rule update.** "She is too fidgety" is not one
retake: amend her rule (fewer object tasks, slower tempo), then re-render the open shots
that carry the old one, and QC the rest against the amendment. Do not ask again on the next
bin.

## QC: the average performance

These axes join the ones in `qc.md`. A take fails on them, or stays `in_progress` with the
axis named, exactly as on hands or head count. Judge from `detail` on a still and from the
`frames` sheet on a clip, and judge the clip's **first second and last second separately**:
the first for the moment before, the last for whether the emotion arrived or was dumped.
Always look at the person who is **not** speaking.

| failure | what it looks like |
|---|---|
| **default stance** | feet parallel, weight centred, arms symmetrical, hands at the sides or clasped in front |
| **lens lock** | eyes to the camera when nobody in the scene is there; **mutual stare**: two people locked eye to eye for the whole clip |
| **stock face** | catalogue smile, open-mouth surprise, crumpled cry, bared-teeth anger |
| **instant emotion** | the whole feeling present in frame one, no onset, no fight, no leak |
| **gesture-per-word** | hands beating every syllable; **nod loop**; **sway**: the standing idle oscillation |
| **mannequin idle** | the non-speaker holds perfectly still, or blinks and nothing else |
| **neutral start** | a sequence head or a post-cut shot with no trace of the moment before |
| **signature missing / drift** | the character's recorded specifics absent, or changed with no story reason |
| **overdirected** | three arbitrary choices in one shot, everyone busy; the fix is **fewer** clauses, not different ones |
| **still-to-clip inheritance** | a default-stance keyframe gave a default-stance clip: fix the still, in the still's bin, before any clip retake (the order in the main skill: still, QC, then clip) |
| **wrong fight** | the prompt said "trying not to cry" and the take sobs; the model played the feeling. Re-render one notch quieter, same seed, before changing anything else |

What a pass looks like: asymmetry somewhere in the body; the hands have a job; the
emotion has a before and an after inside the clip; the first second carries the last
shot's residue; the character's signatures are present and nobody else's are.

**Honest limits** carry over from `qc.md`: you cannot hear, so delivery, timing of a line
and whether a pause is right stay a **please listen** item for the user. Belief (Katselas's
last tool) is the one thing only a viewer can judge; when the frames are clean and you
still cannot say whether it reads as a person, the take is `in_progress` with "please
watch: does she read as a person" open, not a pass.

**The note.** Add one line to the `qc.md` template:

```
performance: <tools used in the prompt> / natural: <what read as a person> / average: <what read as the model's default>
```

## Editing a prompt: the procedure

1. Read the bin's prompt, its references (`bin <id>`), the workflow's grammar (`guide
   <workflow>`, then that workflow's section of `recipes.md`), and the character's rule
   in `rules`. If there is no rule yet for a principal, propose one (above) before you
   write the shot.
2. Name the **event** in one concrete sentence. If you cannot, the shot is not ready.
3. Decide whether the shot is a **head** (sequence start, scene change, time jump,
   post-cut residue). Heads get the moment before, always.
4. Choose at most **three** tools: Specifics (always, from the signatures); one of
   Arbitrary / Use of Objects / The Cover; the Moment Before when the shot is a head.
   An emotional beat uses the fight table instead of a feeling word.
5. Insert **one clause per tool**, in the workflow's grammar. A still gets pose and
   object state; a video gets the beat shape (`opens on ..., then ..., ends on ...`). Keep
   every existing constraint, handle, continuity word and the dialogue's CAPS stress;
   leave out-of-frame wardrobe undescribed (`recipes.md`); never add a pose label or a
   competing crop directive next to a bound pose plate.
6. `check --bin` after the write. Put the tools used in the `generate --reason`
   ("Specifics + object task + moment before; same seed as take 3").
7. QC against the table above, and the `performance:` line in the note.

### Worked example

Still (keyframe), before:

```
medium shot, front three-quarter, eye level, static camera. @sally.png stands at the
kitchen counter, looking at @jim.png, upset. morning light.
```

After (Event; Specifics; Use of Objects; The Cover in place of "upset"):

```
medium shot, front three-quarter, eye level, static camera. @sally.png at the kitchen
counter, weight on her left hip, arms folded low, a folded receipt held between two
fingers of her right hand. She is smiling slightly at @jim.png with her lips closed, eyes
steady on him. morning light.
```

Video, before:

```
Sally confronts Jim about the receipt. She is angry and cries. He looks guilty.
```

After (Moment Before; Event; the fight for both; Alternatives; one Arbitrary; Jim's
contrast from the book):

```
Opens on @sally.png still turning the folded receipt over in her fingers, not yet
looking up. She looks up at @jim.png and asks, level, <d>[English] Whose is THIS.</d>
Her jaw sets; she blinks hard and looks at the window, not at him, and does not cry.
@jim.png stays easy, smiles, and his eyes go to the door before he answers. She starts to
put the receipt down, stops, and keeps it. Ends on her still holding it, him still
smiling.
```

Three tools on her, two on him, one arbitrary action in the shot (keeping the receipt),
nobody shouting, nobody crying, and the clip has a first second and a last second that
differ.

## Where this fits

Read this skill before a `propose-shots` batch that puts people in frame (after
`filmmaking.md`, which decides the shots), when a take fails on performance, when the
user asks for "more natural", "less stiff", "less AI", and at the head of every sequence.
Plates, props and locations never need it. What becomes a habit on a real project goes to
`thm-agent/skill/thm-agent/skill-improvement-notes.md`, the same place as the main skill's
notes; a recipe that passed goes to `project-recipes.md` with the tools it used named.

Sources: Milton Katselas, *Acting Class: Take a Seat* (Phoenix Books, 2008); Beverly Hills
Playhouse, "The Acting Checklist" (bhplayhouse.com/the-acting-checklist/); Film Book Notes
review of *Acting Class* (filmbooknotes.blogspot.com, 2008). The crying rule is Mike's
standing note and classic craft, not a Checklist item.
