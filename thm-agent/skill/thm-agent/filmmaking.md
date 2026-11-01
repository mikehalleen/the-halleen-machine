# Filmmaking: shot grammar for generated clips

The clips are 2-6 seconds and each is one shot. A story beat is not "a clip where X happens";
it is a shot size, a camera position and one action, chosen so the viewer learns what the beat
needs them to know. This file is the grammar. Use it before every `propose-shots` card and
before titling any shot: the one-liner on the card names the size, the angle and what the
viewer sees, not just what the character does. (The title is a few words for the beat, "he
agrees"; the server numbers the shot, and the title never carries a number.)

## Introducing a character

The intro shot is the one the audience uses to recognise the character for the rest of the
piece. It has to give them a clear look:

- **face readable**: front or three-quarter, eyes visible, nothing between the lens and the face;
- **medium or close**: waist-up or tighter; a wide shot introduces a place, not a person;
- **held**: the character is in frame from the first frame, the camera is still or on a slow
  push-in, and the shot lasts long enough to read (2 s minimum on the face);
- **one thing to learn**: what they wear, what they carry, how they stand -- the detail the
  story will use.

Never introduce a character by an entrance from the frame edge, from behind, in silhouette,
in a wide, or mid-action with the face turned away. Those are fine *after* the intro; as the
intro they leave the viewer not knowing who they saw. "She walks in from the side" is a
blocking note, not an introduction. If the beat is "we meet Sally", the shot is Sally, front
three-quarter, medium, still, looking at or past the lens, and the prompt says so.

How to prompt it: name the size and angle first ("medium shot, front three-quarter, eye
level, static camera"), then the character by handle, then the one detail, then the light.
Keep the action tiny (a breath, a turn of the head toward camera) so the face stays readable.

## Establishing where we are

Before the audience can follow action they need the space: the place, the time of day, where
the people are in it. One wide shot does it -- the car on the road with the landscape around
it, the room with the bed in it. It goes first in a sequence, or right after the intro, and
again whenever the location changes. Plates (empty locations) are establishing shots with
nobody in them yet; a plate with a person in it at true scale is also an establishing shot.

How to prompt it: "wide shot", the whole subject in frame with room around it, horizon level,
the light stated; no faces need reading here.

## Coverage: what each size says

- **Wide (WS)**: geography, scale, who is where. Emotion is small.
- **Medium (MS)**: body language, what hands do, two people in relation. The workhorse.
- **Close-up (CU)**: one face, one feeling. Cut to it at the moment that matters.
- **Insert / detail**: the thing itself (the key, the wheel, the hand on the door). Explains
  an action without a face.
- **Over-the-shoulder (OTS)**: one person's view of the other; who is being addressed.
- **Point of view (POV)**: what the character sees, from where they are. Puts the viewer
  inside them. Hood mounts and dash cams are near-POV.
- **Reaction**: a face responding to what just happened. A beat with two people usually needs
  the action and the reaction as two shots, not one.

A beat is covered when the viewer can tell where it happens (wide), what happens (medium or
insert) and how it lands (close-up or reaction). Not every beat needs all three; every beat
needs at least the size that carries its information.

## The line, screen direction, eyelines

Once two people (or a car and the road) are set up, the camera stays on one side of the line
between them, so left stays left across cuts. The driver on the left of frame stays on the
left in every shot of the pair; the car travelling right keeps travelling right. Crossing the
line makes people seem to swap seats and cars seem to turn around. In generated clips the
line is decided by the prompt: state which side each character is on ("Sally on frame
left at the wheel, Jim on frame right") and keep it the same in every bin of the sequence,
and state the car's direction of travel.

Eyelines: when A looks at B in one shot and we cut to B, B is looking back along the same
line (screen right to screen left). A close-up of a face has to look somewhere: name it
("looking off frame right at the passenger", "looking straight down the lens").

## Camera movement

Move the camera for a reason: to follow, to reveal, to push in on a feeling, to pull back to
show the consequence. A static camera is not a failure; most intros and most reactions are
still. Name the move and its motivation in the one-liner: "slow push-in on her face as she
realises", "tracking alongside the car, keeping pace", "hood mount, the road coming at us".
Two moves in one 4 s clip is one too many. A move that starts in the first frame needs the
subject already in motion (see hot starts); a move that starts from rest spends the first
second getting going.

## Beat to shot: setup, turn, payoff

Every story beat has a shape: the state before, the change, the state after. Shot-list it
as:

1. **setup** -- who and where (wide or medium, the line established);
2. **turn** -- the action or decision, on the person doing it (medium, insert or POV);
3. **payoff** -- what it did, on the person it did it to (close-up or reaction).

One action per clip. "She walks up, moves him aside and gets in" is three clips or one clip
that only shows the last of them with the others implied. Choose, and say which.

## Continuity across clips

Each bin renders on its own, so continuity is the recipe's job. Before a sequence card,
write down and keep identical in every prompt: wardrobe per character (the same words), the
props they hold, which seat and which side of frame, the direction of travel, the time of
day and the light, the weather, the state of things that changed (the door that was opened
stays open). Reference bindings carry the look; the prompt carries the state. When a beat
changes something (she is now driving), every later prompt states the new state.

## Hot starts and pacing

A clip that must begin at full energy starts from a keyframe that already has it: the car
at speed, the mouth already open, the hair already blown. Prompt the first frame as the
middle of the action ("already at speed", "mid-scream", "the door already swinging"), never
the beginning of it. A 4 s clip holds one action and one reaction at most; a 2 s clip holds
one. Longer clips are for a move that needs the time (a slow reveal), not for more events.

## Dialogue lines: mark the stress in CAPS

A spoken line in a prompt carries its intended stress as one word (rarely two) in
CAPS, the rest in ordinary case: `says <d>[English] You know what YOU can do.</d>`.
Write it that way in every dialogue prompt, even when the stress seems obvious,
for two reasons. The model reads the cue, and the stressed word is where a line's
meaning turns ("YOU can do" is a dare, "you can DO" is a boast). And the audio
tool's `emphasis` verb checks the take against exactly that word: a line with no
CAPS word has nothing to be checked against, and a line whose stress landed
elsewhere is a re-render with the cue moved, not a shrug. Keep proper nouns and
initialisms out of the convention (`I`, `TV`, `OK` are not stress); when a line
needs two stressed words, give each its own clause.

## The checklist before a shots card

For every item: size named; angle and camera named; who is in frame and on which side;
what the viewer must be able to read (a face, a hand, the road); one action; the state that
carries over from the last beat; the reference it starts from. If the beat is an intro, the
face is readable and the camera is still. If the beat is two people, there is a reaction.

## Two people in a car (researched for project-x)

Standard coverage, from the outside in: the two-shot through the windshield from a hood
mount; a single on each person through their own side window; French overs from the back
seat; a rear two-shot from the back seat. In a car there are only mediums and close-ups.
A **French over** is the over-the-shoulder for two people facing the same way: the camera
sits behind them and shoots over the INSIDE shoulder (the one nearer the other person) onto
the other's face as they turn toward each other. It is the natural single for car dialogue
because nobody can stand in front of the actors. Always name which seat each person is in:
in a left-hand-drive car the driver is in the left seat, so a French over onto the driver
is shot over the passenger's left shoulder, with the driver on the left of frame.
