# Recipes: prompt and reference craft for THM 2.x

Hard-won rules from 1.x projects (dance-crew 1/3/ugc, crash-test-dinos, one-straw,
settleman-law-ad, ugc-skincare) and the first 2.x project (van-halen-movie), restated
in bin terms. A bin is a recipe (workflow, prompt, negative, references, loras,
length_s) plus a gallery; renders land as numbered takes; `selected` leads the bin.
References are bound by media id and addressed in the prompt by handle (`@mara.png`,
`@mara.png.2`). On a video workflow, reference position 1 is the start frame and position 2
is the end frame. Per-project skeletons live in `project-recipes.md`.

## Image prompts

- Describe what one frame shows, not what the subject means to do: `medium shot of person seated in chair, hands gripping armrests, body tensed forward`, never `person preparing to stand up`.
- Spell out anatomy, never pose labels: `left leg lifted off ground reaching forward, right foot planted bearing weight, torso pitched forward, slight motion blur on limbs`, never `mid-stride`. If a reader cannot sketch the pose from the text, add detail until they can.
- Structure: `[frame direction] + [subject @handle] + [explicit body position] + [key details]`; frame direction first (wide, medium, close up, low angle, high angle, side view, from behind).
- The still is the highest-fidelity moment: faces, readable labels, hands on objects, hero poses go in the image bin; a video push-in never reaches a label the still does not already show.
- Every prompt stands alone: no `as before`, `same as`, `still holding it`, `then`, or references to another bin; name the subject, object, place and visible state in this field.
- Bind every subject to a handle and pair the handle with a visible distinguisher: `green dino @spino.png`, `blue t-rex @rex.png`; never `lead subject` or `both subjects`.
- Parenthetical spatial blocking after the handle mention fixes impossible geometry: `woman shopper @mara.png stands (directly in front of the shelf @shelf.png) which contains several rows of product @lotion.png`.
- Name every human individually with a compact full outfit (top, bottoms, footwear, leg-level items); collective references (`the other people`, `the other four dancers`) spawn extra or wrong people, even for small soft-focus background figures.
- Group roster lock: open with `((exactly five dancers and no one else, five different people, one each and no repeats, no duplicates, no bystander))`, put the featured subject FIRST, name the rest in a tight formation (`in a line, left to right: ...`), never `scattered across`.
- Count emphasis alone does not stop hybrids; add garment-level exclusions: `((no yellow tee on anyone standing, no thigh-high socks on anyone standing))`.
- Describe only what the crop shows; an off-frame garment named in a waist-up prompt migrates onto a visible limb (thigh-high socks became striped sleeves). When a reveal is possible, describe the off-frame wardrobe correctly instead.
- Naming footwear pulls the camera back to reveal it; on a three-quarter crop choose correct shoes over the exact crop (barefoot is a hard failure, a wider frame is a soft one).
- Scale is proportion, not distance: `(((She is at correct human proportion to the doorways, windows, steps and furniture around her)))`; a distance phrasing (`small in the frame`) destroys the pose.
- Describe geometry, never a coverage noun: `two-shot facing each other` forces a face-to-face render through any framing text; delete the noun and write `a LOW WIDE from chest height with the tabletop dominating`.
- Eyes-cropped framing is impossible on a wide; cut the frame entirely below the faces instead: `the top edge of the frame crosses their chests so their HEADS ARE ENTIRELY ABOVE THE TOP EDGE; we see torsos, arms, hands, the tabletop, chairs and floor only`.
- Rotation words fail on SDXL; front-load the view, weight it `(rear view seen from behind:1.5)`, describe what the camera can and cannot see, and negate the front-view default per angle.
- A bound pose reference owns the framing: tag each pose plate full / three-quarter / close by eye, write `(((match the crop and camera distance of @pose.png exactly))) -- do not pull back to show more than @pose.png shows`, and never add a competing crop directive, `is standing`, or `natural relaxed pose`.
- A pose plate carries no gesture: append its one-line gesture description (` - both arms raised above the head, chin lifted`) or the gesture vanishes (0/3 without, 3/3 with).
- A still used as a video start frame is staged at the BEFORE state: `puck held a few inches away, not yet attached, clearly separate from the phone`, never the completed action.
- A still for a motion beat is frozen mid-action: hind legs extended, body pitched forward, motion blur, dust; a calm standing still cannot lead into a sprint.
- Two subjects side by side facing forward render facing each other; frame same-direction pairs as a low three-quarter REAR tracking view with `backs to camera / running away to the right` and anti-face-each-other negatives.
- Klein phone/object-toward-camera stills default to selfie POV: write `HER OWN ARM -- shoulder, elbow, forearm, hand` plus `knuckles and fingernails toward camera, fingertips curled over the edges, thumb hidden behind the phone, no palm anywhere`; grip is a coin flip per take, QC the hand every time.
- To show a graphic on a screen, bind the graphic as a reference and write `the phone screen clearly displays the app interface exactly as shown in @app.png`, plus `turned SCREEN-SIDE FLAT toward the camera, only the glass front visible`; never composite by hand.
- Placeholder marker: render a location plate with a small chromatically loud object at the target spot (a red soda can, a red card sized to the artwork), then in the composite bin write `positioned exactly where the red can was (the red can is removed and completely gone)`.
- Skin finish: positive `matte natural skin finish`; negatives `oily skin, skin shine, dewy highlight, specular highlight`.
- Product stills: name a fictional brand, bottle type, color and label; negatives `gibberish text, nonsense letters, illegible logo, blurry text, real brand names, wrong bottle type`; readable labels are not guaranteed, say so.
- Anatomy negatives when limbs split: `extra arms, third arm, disembodied hand, phantom limb from frame edge, duplicate person, second person`; and stack the positionally specific version inline in the positive text: `left arm must NOT reach toward camera, no left hand at bottom-right corner, no second hand`.
- Avoid industry nicknames and metaphors (`nodding-donkey` rendered a horse sculpture) and avoid `reference photograph, plain neutral background` unless a studio product still is wanted.
- One axis per take: change framing, one clause, one binding, or one reference swap, never all at once; say what changed before regenerating.

## Video prompts

- Describe what happens during the clip: motion, transformation, camera. Never frozen pose text, never character or location prose the references already carry.
- The video model has no memory, motivation or story position; write sequential present-tense literal description (`First he does X. When he stops, she does Y.`), body-part-level moves, and translate every concept into what bodies, faces and camera visibly do. Longer literal prompts outperform terse ones.
- Banned: relational verbs (`she answers with`, `trades hits`), concept labels (`button ending`, `the finale`, `hero-shot style`), and temporal words (`final`, `landed`, `ending`, `button`) which leak end-state into the whole clip.
- Category adjectives get defaulted: `colored smoke` renders white; write `thick magenta and teal smoke`.
- Prefer literal over metaphor and stack the negation of the likely misread inside the positive text: `brief dim-bright flicker in the overhead light, no flame, no fire` (the simile `like an aging flash unit` produced fire).
- Name the visible change, not just the verb: `each stride visibly advancing him closer to the camera` (bare `walks toward camera` came back near-static).
- Name a direction for every significant motion (falls, spins, throws, turns), relative to the start frame's orientation, or the model invents one.
- Name the end state for any motion that must land (fall, jump, spin, sit-down, arrival); pair end state with direction for any rotation, and prefer single-axis motion over flips and inversions.
- Use positive per-frame invariants, not negations: `in every frame the subject stays airborne, feet off the ground` beats `no landing`; restate the invariant inside the ending sentence because drift is worst late.
- Large translation needs a deadline and a ruler: `keeps rising through the first quarter of the clip, her sneakers climbing past the backboard until her feet are far above the others' heads`.
- Persistence clauses for anything that must stay put: `there is only ever ONE straw, nothing multiplies`, `the box STAYS COMPLETELY SEALED and never opens`, `the forks REMAIN IN FRAME for the entire shot`; negate the failure (`two straws, open box, forklift disappearing`) and add `cut, dissolve, cross-fade, scene change, jump cut, new angle` with `ONE single framing for the entire shot`.
- Frame hold: start-only clip, `on-screen for the first two thirds, may exit only in the final third`; start+end clip, `every subject in both anchors stays fully visible inside the frame for the entire clip`; single talking clip, `stays fully in frame the entire clip, keeps the camera on her face, does not turn the phone away`.
- Say whether the CAMERA or the SUBJECT moves: `the camera pulls back and rises away ... the dinosaurs stay in place, growing smaller as the camera pulls away, clearly the camera moving not the dinosaurs walking`; give the in-place subjects active business.
- The landing clause is a claim about the bound end frame; if the end frame is wide, do not write a close-up landing or the model inverts the move. Anchor the START instead: `camera starts at the far side of the room and pushes straight forward, the subject growing larger, never pulling back, never retreating, never rising`.
- Prompts extend the start frame, never contradict it: a floodlights-slam-on beat on an already-lit plate, or true top-down on an oblique plate, makes the model wander or invert; regenerate the start frame to support the beat.
- Restate each subject's identity every clip (ethnicity, skin, hair, full outfit) and add `Every dancer keeps exactly the same face, hair, skin tone and clothing as the first frame for the whole clip`; small-scale subjects lose fine costume detail systematically, so dedicate a clause to that garment: `her arms fully covered by the black-and-white striped sleeves for the whole clip`.
- Group motion: a distinct named move per subject on the same beat (one kicks, one spins, one pops) reads real; perfectly synchronized crews read fake. Duet closers end side by side, never body-overlapped (the occluded face smears).
- Effects need a visible source: `smoke-bomb canisters on the court behind her jet thick opaque magenta smoke that rolls across the court`; never thin, venting, drifting, or synced to a body move; sources wide of every subject. Particles need weight: `coarse heavy powder grit that hang for a beat then fall straight down in streaks, dusting the court floor`.
- Signature prop, set or aerial actions go in the start frame, not prompt text; the video only has to carry them.
- Scripted end-freezes over-hold; give the last beat residual motion (`shoulders keep bouncing while the fist holds`) or accept a second of dead air.
- Near-lens fists spawn props: `hands stay empty and open; she never reaches toward the camera` plus object negatives.
- Scope directives to their layer: still notes (`keep hair out of her face`) go in the image bin, motion notes in the video bin, doctrine in the reusable description.
- Audio motifs: define each recurring sound once as a constant string and reuse it verbatim with only a distance qualifier (`close and loud in the foreground` / `clear and present at working distance` / `thin and far off in the distance`). Wordless film: `There is no music of any kind anywhere in the shot.` positively plus `music, musical score, soundtrack, song, singing, melody, instrumental` and `spoken dialogue, intelligible speech, narration` in the negative.
- Match scope to length_s: 1-2 s single action; 3-4 s full action; 5-6 s chain of actions or a transformation; 7-10 s multiple beats. A beat-count choreography prompt implies a minimum length (8 counts at 120 BPM needs 4 s).
- A still-frame QC verdict can invert in motion (a "face cropped, too close" final frame read as an assertive walk-up); flag marginal composition calls as the user's eye in motion decides instead of discarding the take.
- QC video by sampling 6-8 frames per 5 s, counting people in each frame against the intended cast, and comparing each subject's identity at takeoff versus landing on airborne beats; with an end frame bound, judge the true last frame before failing a late-frame wobble.
- Two failed prompt-side attempts on the same defect means the composition is the problem: reduce what is in frame (tight insert, hands only, forks only) instead of rolling again.

## References and identity

- Identity without a LoRA is a bin: generate a hero portrait in a bin tagged `character`, select it, and `bind_reference` its selected media into every bin that needs the face; expect minor drift between takes.
- Handles come from the source bin's name; write them into the prompt and after any prompt write re-read `bin <id>` to confirm every `@handle` is bound (the server only warns at render time).
- A location is a bin tagged `location` whose selected media is bound; a product is a bin tagged `character`, its prompt describing only the object (placement belongs in the composite bin's prompt).
- Empty references are the root cause of most "it keeps changing" complaints: audit every bin's reference list before treating drift as a prompting problem; the plate you need often already exists in another bin.
- Prefer an existing real plate or an in-project crop of an approved take over generating a new reference; a generated reference from technical prose fights the model prior.
- Scale comes from the reference's apparent size, not words: a macro product plate produces a fence-post-sized straw in every scene. Bind a scale-anchored plate (a known-size object in frame) and use comparative language (`the same length as the crushed bottles beside it, no thicker than a pencil`) plus negatives for the failure mode and the anchor object.
- Scale dummy plate: an empty location plate gives Klein no human-scale anchor and subjects come out giant. Render the location with one ordinary person in the FOREGROUND, state the relationship (`the fence stands only chest-to-shoulder height next to the person`), bind that plate, add the scale language and a count lock to the composite prompt; the model drops the dummy. Both halves are required.
- Face vs costume conflict: bind two references for one subject (a close portrait and a full-body costume plate) plus the location.
- A close portrait beats a wide take as an identity reference; on H3 the wide take never gave Mike a face, the close portrait did.
- Same-spot series: after the first take is approved, erase the subjects from it in a Klein bin (`remove the dancers, keep every structure identical`) to mint a master plate and bind that in every later bin; QC each new still against the previous selected still, not only the plate.
- Camera-travel beats: never bind the sibling anchor's render as the location (it locks the background and the interpolation reads as the subjects moving); bind the location plate and give each anchor its own framing.
- Start+end pairs must share every element (napkin, glass, charger) and the same lighting language; a missing element or a warm end frame over a cool start produces a mid-clip dissolve or a color drift. Compare subject left-to-right order in both anchors before rendering a multi-subject pair; a swapped order becomes an identity morph.
- Two structurally identical bound stills give the interpolation nothing to move toward; vary the binding, not the identity.
- Reference plates get the same limb-count and costume audit as renders; a missing arm in a plate propagates into every bin that binds it.
- QC against the bound media, never the plan, roster constant, or your memory of earlier prompts (the "charcoal puck" was pink in its reference).
- Costume canon comes from the user-named take; majority vote across recent takes amplifies your own earlier mistake.
- Do not pre-pin a value the recipe would inherit; `rebind_reference` swaps the picture and keeps the handle; `recall <media>` restores the exact recipe that made a take (`--generate` renders it again on the same seed).
- Seeds: spread them when exploring (10000, 20000, 30000); consecutive seeds are near-duplicates. Validate a recipe on one instance of each category (pose framing, venue, subject count) before rolling it out; three seeds on one instance measures seed-robustness, not generality.

## Workflow craft

### Klein (`klein`, `klein_enhanced`)

- Klein has no negative encoder unless `workflows` reports `negative=True` (`has_negative`); on a workflow without one, everything in `negative` is silently inert, so express every constraint positively in the prompt. When a control seems not to work, check that it is wired before theorising about weighting.
- Address every bound reference by handle in the prompt; an unmentioned handle draws an `unused_handle` warning at render and contributes nothing reliable.
- Casting recipe (van-halen): `posed as seen in @mike-og.png, but with his facial identity replaced by the likeness from @untitled.png; watermarks removed, skin tone and coloring retained from @mike-og.png, nose preserved from @mike-og.png like a very well done lifelike prosthetic, seamless facial integration matching body and facial skin tone and scale ... Match the film grain between the face and other parts of the skin. Do not make the face too big.`
- Klein does symmetric edits, object removal and face close-ups well; it cannot recolor one leg of a garment (both legs change every time) and resists re-framing a full body into a waist-up crop; crop or pixel-edit those instead.
- Multi-view sets: derive every other angle from the front plate in a Klein bin (`the exact same garment shown in @front.png, identical cut, fabric, colour and print scale, on the exact same headless mannequin`) rather than re-rolling text-to-image.
- Unfilled reference slots are safe; bind only what the beat needs.
- **A radical reframe needs a nearer plate, not a better prompt.** Asking for a car's interior off an
  exterior three-quarter plate returned another exterior twice over; Klein holds the plate's composition.
  Rendering a wider or higher view first (here a straight overhead that already showed the cabin), binding
  that take as a second plate and citing it for what it shows (`the cabin is the one in @car.png, seen from
  directly above in that image: the same tan leather seats ... the same wood-rimmed wheel`) landed the
  interior first try. Same shape as the multi-view rule: derive each new view from the nearest view you
  already have.
- Multi-view sets of an object hold identity well when the object spec is repeated word for word in every
  prompt and only the viewpoint clause changes; ten angles of one car came back with the same paint, chrome,
  wheels, interior, ground and light.
- **Every person you mention appears.** "His shoulder at the right edge of frame" and "turned toward his
  friend, who sits off screen" each spawned a whole extra person (online-gaming). A single is written with
  nobody else named and the empty side described as set (`the left of frame is just the blurred brick wall
  and empty bar`); a shot that needs the other character is a full two-shot.
- **Lock left/right by wardrobe, not only by name**: `On the LEFT: Tommy @tommy.png, the man in the BLUE
  HOODIE ... On the RIGHT: Dev @dev.png, the man in the RED JERSEY`. Names and handles alone let Klein swap
  sides.
- **Say what is there, not what is bare.** `bare wrists with no watch` stripped Dev's white long sleeves;
  `his white long sleeves come down to his wrists, no watch, no jewelry` kept both.
- **A set with a counter (bar, desk, shop):** the counter is the line. Write it on every keyframe: `the
  camera stands on the bartender's side; the counter runs across the bottom of the frame between the camera
  and the customers; both men sit on the FAR side; nobody is on the camera's side`. Off-axis angles move
  the camera along the counter; a steep diagonal lets the subject slide onto the camera side, so keep the
  near edge of the counter crossing the frame in front of them.
- **Screen text is its own asset.** Render the app screen as a flat 720x1280 still with two to six large
  strings spelled out and no small print (`BROKER`, `LIVE`, `21 - 17`, `PLACE BET` came back exact first
  time); bind it and write `the screen clearly displays the app interface exactly as shown in @app.png`.
  Text written into a scene prompt garbles; fine print on a title card failed three times and was set in
  Pillow on a copy instead (`upload --from`).
- One phone needs `exactly one phone ... There is only one phone anywhere in the shot`; otherwise a second
  appears on the bar.

### Krea 2 turbo (`krea2_turbo`)

- **Krea draws every noun in the prompt, negated or not.** `TVs mounted behind the camera, no screens on
  the walls` gave three lit TVs. Describe only the effect (`strong flickering cool blue-white light from
  somewhere behind the camera`) and never the object.

### MiniMax H3 (`minimax_h3_r2v`, `minimax_h3_r2v_enhancer`)

- The prompt is the enhancer's structured form: `subject_definitions` (one `<Subject n> (@handle)` or `<Video n> (@handle)` per reference), `summary`, `retention_analysis` (per subject: `fully_preserved` or `weak_reference` with what is and is not retained), `detailed_description`, `overall_soundscape`, `non_diegetic_music`. Read `guide minimax_h3_r2v_enhancer` before writing one.
- Every subject definition says what is taken from the still and what is not: `From @ed.png only the face and hair; not the dark jacket, not the wood-panel room, not the pointing pose, not the daylight.` Without the exclusion the whole portrait leaks (blue shirt, bottle bass, thumbs-up).
- The `detailed_description` ends with the mandated negatives line: `no added cuts, no subtitles, no watermarks, no on-screen or burned-in text`; the app's echo check once refused a rewrite for quoting it, so keep it as the tail, not a heading.
- A bound clip is a pixel source unless declared `weak_reference` with its beats written out in the description; declared that way it re-performs the clip with the subjects' faces. A render of the same length as the clip replays the clip with its original faces (3 s = 2.9 s clip), and a 6 s single-subject clip outweighs any still no matter what the text says: unbind it and write the performance out.
- Describe the lighting of the still positively (`a stage lit by a bright white front key light ... no colored wash`) and forbid the wash in the negatives, or the light drifts red after the first second.
- Faces need `Each face stays sharply resolved, solidly attached to its head, and unmistakably the person from its still at every distance and angle`; a lunge into the lens is capped at `for one quick beat only`.
- Finger work reads only when described per finger and the camera pushes in on the neck (`index, middle, ring and little fingers each pressing their own string on their own fret in turn`); two-hand tapping did not land in two passes.
- Ladder: quarter at 3 s to prove the structure, quarter at 5 s, full size at 5 s, then 8 s full size once the beats hold. A full-size take is a new render, so QC it on every axis again.
- **Keyframe-first clips (online-gaming):** bind the shot's own approved keyframe as `<Picture 1>` "the first frame of [Shot 1]" plus the character portraits as `<Subject n>` for faces; summary `[keyframe completion]`. Two-shots carry the line and the side of the counter as a sentence, `Exactly two people in the shot.`, and the per-frame invariants.
- **The soundscape is a prompt too.** `faint game commentary from screens off camera` put a TV on the wall mid-clip. Keep sound lines free of objects you do not want seen, and state the set as unchanging: `The walls stay exactly as in the first frame ... nothing new appears on them. The light stays the same cool blue and warm amber from the first frame to the last, with no colored wash` (a phone's green glow flooded the frame without it).
- **A phone has one screen.** When a character turns a phone toward someone, H3 puts video on the side facing the lens. Keep the screen toward its holder and write the back: `plain solid matte black with a small camera bump, no screen, no light and no image`; the other character leans in to see.
- **Hand-to-hand contact does not render.** A slap on a reaching hand came back as a hand-hold, an escape with the wing, then a miss even from a start frame a split second before impact (3 takes, cap). Stage contact beats as one object moving (pull the basket away), a cut with the hit as sound, or another model.
- Say the tap: `at about one second the index finger of his other hand ... comes down and clearly presses the glass in one deliberate tap`; a bare `taps the screen` came back as a man holding a phone.

### LTX 2.5 (`ltx2.5`)

- Plan every clip as start-frame-only: talk-in-place, walk-away/recede, exit-past-camera. Talk-while-approaching is not achievable by frame anchoring; start close or cut wide-to-close across two bins.
- When position 2 is bound it is a target the clip must land on; sample it from the USED portion of a source take (respect any trim) or the clip walks the subject out to match an empty end frame.
- Dialogue needs a chest-up start frame; full-body framing makes the face too small for the audio-to-lip coupling and reads as dubbed.
- Voice: describe it explicitly and identically in every clip of a speaker (`a bright, dry, matter-of-fact woman's voice with an American accent, quick and a little deadpan, speaking at a natural quick pace`), especially faceless clips, or the voice and even the gender change per clip.
- Line shape: `Audio: <voice description> says, '<line>'`; the agent cannot hear audio, so never mark pass on the audio axis; hand talking takes to the user.
- Ending: anchor a target expression (`presses her LIPS TOGETHER into a cool, knowing SMIRK, chin slightly down, one eyebrow raised`), never a prohibition (`NO laugh` failed three seeds); budget 3-5 takes for a specific ending and QC the final second first.
- Post-line instability: the subject invents an exit about 0.5 s after the line; keep length_s short and offer a trim to speech end + 0.45 s, or re-stage the start frame off-axis so there is no corridor to dolly into.
- Walk-and-talk renders slow motion regardless of prompt; offer a 1.2x pitch-preserving remap in post rather than rerolling.
- Never mention text in any field, including the negative; naming it reinforces gibberish on plain surfaces. Fix with a tighter crop.
- Frames round down to 8n+1: 24 fps lands every whole second, 25 fps only at 8.00 s; keep the project fps and the workflow fps equal.
- Static i2v is usually workflow-side (the img-to-video conditioning strength), not the prompt; report it as a workflow-strength matter instead of rerolling text.

### WAN 2.2 (`wan2.2_14B`, `wan2.2_5B`)

- Posture is baked into the start frame; a seated start never stands up and a standing start is how costume fidelity survives a desk-to-dance beat.
- Seed rerolls change choreography a lot; a direction failure is often a seed failure, so reroll once before rewriting.
- 3 s is the floor for start-only clips (2 s hard-crashes ComfyUI); 5 s is the recommended length; 8 s max.
- `quarter: true` is for drafts; a quarter take cannot be upscaled, so the final is a full-size regen (a new render, QC again).
- Stills QC for real speed: grounded feet plus motion blur; running reads as gliding unless per-limb ground contact is written: `thick hind legs pump up and down in a full running stride, each foot strikes the pavement in turn with heavy footfalls, weight landing solidly on the ground every step, no sliding no floating no flying`.
- Anti-slow-mo prefix on impact clips, verbatim: `Real-time maximum sprint speed entire clip -- camera and subjects stay at full racing velocity through the impact frame, never slow motion, never bullet time, never speed ramp or deceleration into impact, collision at full sprint not slow-mo.` with negatives `slow motion, slow-mo, bullet time, speed ramp, deceleration on impact, freeze frame`.
- Impact lands in the final 20-30 percent; the first 70-80 percent shows the obstacle intact at full speed; `the obstacle is destroyed and stays destroyed - it never reassembles`.
- Framed artwork on a push-in: `the {medium} {subject} is clearly visible inside its {frame} from the very first frame to the last, never obscured by glare, reflection or blur, the artwork never changes`.
- Mirror-heavy rooms: exit toward the camera only, `camera LOCKED (no pan), mirror kept in the background behind them`.
- Formation rotation cannot be end-frame conditioned; keep the anchors stable and describe a camera orbit instead.

### Seedance 1.5 pro (`api_bytedace_seedance1_5_i2v`, `api_bytedace_seedance1_5_flf2v`)

Partner API nodes (`ByteDanceImageToVideoNode`, `ByteDanceFirstLastFrameNode`, model
`seedance-1-5-pro-251215`). i2v takes a start frame only, flf2v start and end. Baked 1080p,
adaptive ratio, `camera_fixed` and `generate_audio` off, default 4 s; the bin's `length_s`
drives the node's duration (vendor range 4-12 s, whole seconds). No negative encoder, no
LoRA, no handles: the prompt is plain prose and `@tokens` in it resolve to the referenced
bin's plain name, so write the subject in words. Vendor rules below are from the model's
prompting guide, not yet proven on a THM take -- probe before trusting.

- The frames already say what the scene looks like; the prompt is what MOVES. On flf2v write
  the bridge (motion, camera path, pacing) rather than re-describing either frame.
- Vendor ordering: composition, then subject, then camera, then mood. It locks the frame
  before inventing motion; contradictory clauses ("peaceful" + "frantic") produce artifacts
  with no error.
- Audio is generated from the prompt when `generate_audio` is on: dialogue in quotation marks
  first (`"Ladies and gentlemen..."`), then comma-separated ambient sounds. THM leaves audio
  off on both workflows, so drop sound clauses unless the workflow file changes; never claim
  an audio QC either way.
- Prefer `camera_fixed: true` phrasing in the node over a "static camera" clause when the
  subject should move inside a locked frame; temporal artifacts answer to less motion in the
  prompt, not more words.
- 5 s transitions stay tighter than 10 s on flf2v; take the longer duration only when the two
  frames are far apart.

### Seedance 2.5 (`api_seedance2_5_r2v`)

Partner API node (`ByteDance2ReferenceNode`, model `Seedance 2.5`), 4 image slots and 2 video
slots, no start/end frame. Addresses by token: a bound `@handle` is rewritten at submit into
`Image 1` / `Video 2` (positional, no `@`, no angle brackets -- unlike H3's `<Picture n>`),
and `check --bin` warns on an unused handle. Baked 720p, adaptive ratio, default 5 s with
`length_s` driving it, `generate_audio` ON, `video_editing` off, watermark off.

- Label every reference and name the part you want, narrowly: `Image 1 defines the woman's
  face and green jacket only`. A wide label drags the reference's background into the shot;
  an unlabelled reference is the documented way to quietly ruin a take.
- Say which part to USE rather than listing what to leave out; the model has no negative field
  and THM reports `has_negative=false`.
- Several angles of one thing need saying so: `all three images show one lamp`.
- Split the runtime into whole-second timestamp beats with no gaps and one action each
  (`0-2s: ...`, `2-5s: ...`). An overstuffed range drops beats; an empty one gets filled with
  something you did not ask for.
- One camera move per beat, from the plain vocabulary (tracking, slow push in, wide
  establishing, handheld, aerial, dolly forward, orbit, rack focus, low angle, top down).
  Stacked moves are a listed failure mode.
- A bound clip is cited for what it contributes and nothing else: `Video 1 defines the camera
  move and the pacing only`.
- Audio is on by default, and the vendor syntax brackets it by kind: music in fullwidth
  parens, effects in angle brackets, speech in braces, captions in lenticular brackets. Write
  it if the clip wants sound, and hand the audio verdict to the user.
- Describe change over time, not a still; a static description is the first listed mistake.

### Kling start-end (`api_kling_flf`, `THM-api_kling_flf`)

Partner API node (`KlingStartEndFrameNode`), start and end frame, `pro mode / 5s duration /
kling-v1-6`, 16:9, `cfg_scale` 1. Two THM gotchas before writing anything:

- **Duration is baked.** The node carries no `duration` field (the mode string holds it), so
  `length_s` on the bin is ignored and the take is 5 s. 10 s needs a different mode in the
  workflow file, which is the user's to change, not yours.
- **The negative is unreachable.** The graph HAS a `negative_prompt`, but the scan reports
  `has_negative=false`, so a bin's `negative` never reaches it. Kling is the one model here
  that honours negatives, so that is a real gap: say so rather than writing negatives that go
  nowhere.
- Prompt formula: `[shot type] of [subject] [action], [environment], [camera movement],
  [lighting/mood], [style]`, around 40 words; v1.6 wants simpler prompts than later Kling
  versions.
- Camera vocabulary it knows: dolly, tracking, crane/jib, pan, tilt, orbit, handheld,
  steadicam, with a speed word (slow, gentle, rapid).
- Name the end state explicitly on a start-end pair; vague endings are what leave a generation
  spinning at the last frame.

## LoRAs

- `<lora:name:strength>` tags in the prompt are lifted into `bin.loras`; write the exact on-disk filename including `@safetensors.png` unless the name follows the `_high_noise`/`_low_noise` pair convention.
- Read the LoRA's training captions first and mirror the format: trigger phrase in position 1, the trained caption template (energy adjective, subject detail, background sentence, lighting sentence, trained closers), trained tag combos.
- ONE concrete action sentence inside the trained structure helps; several fight it.
- Style/motion LoRA on every clip of the look; effect LoRA on the ONE bin where the effect occurs, strength 0.75-1.0; never the same semantic on both layers.
- A LoRA imposes its trained material and negatives cannot override it (a blast LoRA renders fire through fire-negatives); for a different material drop the LoRA and use pure prompt language.
- A trained close-camera caption line can drag tempo (`The camera angle is low, focusing on his face and arms` slowed hip-hop takes); anchor close framing with the start frame instead.
- Do not spend prompt budget on a prop for the LoRA to act on; name the location and let the trigger fire.

### Seedance 2.5, proven on a real beat (scary, 3 takes)

The vendor rules above hold; two of them decide the take, and one model prior has to be beaten.

- **One action per beat is not advice, it is the failure mode.** A 2 s range carrying three
  actions loses the last two silently. Give the beat that matters its own second.
- **A held state beats an action verb.** `the jaws snap shut` never closed the mouth; the close
  described as parts moving (`the upper jaw comes down onto the lower jaw, the lips meet along
  the whole length of the snout, and the teeth disappear behind them`), restated in every later
  beat and closed with a standing line (`from seven seconds to the end the mouth is shut in every
  frame: no teeth, no tongue and no inside of the mouth are visible again`) did it first try.
- **Scale and containment need saying** on a plate the creature can outgrow: `at exactly the size
  it is in that image, its body filling about half the width of the tank` and `stays inside the
  tank ... never climbs onto the grass`. Without both it doubled in size and walked onto the grass.
- The swallow prior is a crocodile throw-back-and-gulp with the mouth open; budget a second of
  open mouth before any seal.
- `quarter` is a no-op on every partner API workflow (the node carries its own resolution and
  there is no frame-count node), so there is no cheap rung: every take is a full spend. Size the
  attempt by duration instead, and expect the returned size to be the node's, not the project's
  canvas (720p adaptive came back 1470x630).
