# Project recipes: what worked, per project, restated for 2.x bins

Every video recipe below was written against one model and tuned on its takes.
Structure rules carry across models: what goes in the start frame, name every human,
positive invariants, literal present-tense motion, persistence clauses, count locks.
Clause-level tricks are model-specific and do not carry on faith: the anti-slow-mo
prefix, LoRA headers, frame-hold wording, `subject_definitions` blocks, `Audio:` lines.
On a new model each clause-level trick gets one probe take before it is relied on.
Prompt skeletons are verbatim from the selected takes; `__lora:x__` is written in 2.x
form as `<lora:x>` and unicode dashes as `--`. General rules live in `recipes.md`.

## dance-crew

**Project**: five-member dance crew music video, first build; per-location dance beats on a rooftop, a subway platform and a night basketball court, cut to a track.

**Models then**: image `klein_multi_image.json` (Klein) for composites, SDXL `analogMadnessSDXL_xl5.safetensors` (cfg 4.0, dpmpp_2m_sde, karras) for character and location plates; video `THM-api_bytedace_seedance1_5_flf2v.json` (Seedance 1.5 pro first/last frame, partner node) after LTX FLF failed to pin the end frame, WAN 2.2 i2v with `crashtest-dance-v2_high_noise` for the start-only dance beats.

**Models now**: Seedance is superseded and not offered; `klein` for composites, `sdxl_analog_madness` for plates, `wan2.2_14B` for start-only dance beats, `ltx2.5` with position 2 bound for any true start-to-end beat (probe: end frame must share lighting and every element). Keep only the staging lessons from this project.

**Prompt reference now**: `guide wan2.2_14B` and `guide ltx2.5` for the baked enhancer prompts; the vendor's current WAN 2.2 and LTX-2 prompting guides, fetched with permission.

**Reference setup**: one bin per dancer tagged `character` (selected media = the canon plate the user named, e.g. the id41 costume), a trio bin and a duo bin tagged `character` for group composites, one bin per location tagged `location`. A composite image bin binds trio + duo + location; the video bin binds that still at position 1. For the aerial end beat the mid-air still is bound, and the video only carries the motion.

**Prompt skeleton** (start-only, five dancers, per-dancer moves, seq9 vid18):

```
<lora:crashtest-dance-v2_high_noise:1.0> dance style tiktok, hip hop. Real-time full dance speed the entire clip, never slow motion, never speed ramp. At the first moment all five dancers are in mid-air at the top of a jump above the rooftop. They land together on the first beat, knees bending deep. Then each dancer does a different move: the lanky Black man in the black bucket hat, purple zip-up hoodie and black track pants snaps one leg into a high kick, the Black woman with the black afro puff in the teal cropped hoodie and white cargo pants spins once and drops into a low squat, the light-brown-skinned man with buzzed dark hair and a short beard in the orange bomber jacket, white tee and black joggers pops his chest twice, the Asian woman with the pink bob in the knotted yellow tee and black shorts whips her hair in a full circle, and the Black woman with brown skin and long box braids in the cropped red windbreaker and denim shorts over color-blocked bike shorts swings her hips side to side with one arm thrown up. On the last beat each freezes in a different shape. The background shows city lights winking on through warm haze. The lighting is warm. Every dancer keeps exactly the same face, hair, skin tone and clothing as the first frame for the whole clip.
```

Negatives (every dance clip): `subject exiting frame, dancer walks off screen, camera reversing, backward tracking, extra limbs, deformed hands, additional people appearing, extra dancers, crowd forming, people materializing in background, spectators, ((sixth dancer)), duplicate dancer, dancers swapping outfits, outfit changing color, clothes morphing, faces morphing, identity swap, dancers merging, slow motion, sluggish movement, singing, lip syncing, mouthing lyrics, rapping, talking to the camera`

Prompt skeleton (landing on a bound end frame, seq6 vid15, position 2 = the diamond still):

```
The camera starts high above a rooftop at golden hour and glides steadily downward the whole clip toward five dancers: [each dancer named with ethnicity, hair, full outfit], grooving in a tight diamond. All five bounce hard on every beat, knees pumping, arms swinging together, hitting the same move on the same beat, as the camera comes down level with them and their faces fill the frame sharp and clear. Every dancer keeps exactly the same face, hair, skin tone and clothing as the final frame for the whole clip; maintain hair and identity from the final image.
```

**Params**: length_s 4 (2 s start-only crashes ComfyUI; render 3 and trim); quarter true for drafts, full-size regen for the cut; fps 16; seeds spread; a direction failure gets one reroll before a rewrite.

**What it fixed**: posture baked into the start frame (a seated still never stood up on Seedance, three takes and a doubled length bought nothing); positive per-frame invariants restated in the ending sentence instead of negatives (partner nodes ignore the negative); a button that survives ease-out (the last beat gets residual motion: `On the last beat each freezes in a different shape` after continuous motion, not a scripted freeze that over-holds); FLF lighting match (`same cool pink-purple lighting as the wide shots, no warm orange` on the end frame, or the clip drifts purple to orange); fence dismount and hover only rendered when the still already showed them; hat-shape clause `his black bucket hat keeps its short, soft, downward brim and low round crown for the whole clip`.

**Where it does not apply**: the LoRA header and the `never speed ramp` prefix are WAN clauses; do not send them to `ltx2.5` or H3. Partner audio was unusable for scripted lines. `talk-while-approaching` was never solved here.

## dance-crew-3

**Project**: same crew, new outfits, fresh timeline: graffiti warehouse and cyberpunk alley wides, a gravity-defying solo, aerial finale; cut to Quad Speed Bass at 120 BPM.

**Models then**: image `klein_multi_image.json` composites over SDXL `analogMadnessSDXL_xl5.safetensors` plates (setting LoRAs `SDXL-LORA-cyberpunk_str33t:0.6`, `SDXL-LORA-fashion_runw4y:0.6`); video WAN 2.2 14B i2v (`i2v_base.json`, 14 steps, fps 16) with `crashtest-dance-v2_high_noise:1.0` and `Wan-high-waist-dance-V5.safetensors:1.0`.

**Models now**: `klein`, `sdxl_analog_madness`, `wan2.2_14B`: the WAN recipe transfers as written.

**Prompt reference now**: `guide wan2.2_14B` for the baked enhancer prompt; the vendor's current WAN 2.2 prompting guide, fetched with permission; the dance LoRAs' training captions (trigger `dance style <tags>.` in position 1).

**Reference setup**: each location bin's selected media is the scale-dummy plate (`scale.png`: the plate re-rendered with one ordinary person in the foreground so Klein composites dancers at human scale and drops the dummy); a trio bin and a duo/quartet bin tagged `character` hold the new-outfit plates; the composite image bin binds location + trio + duo and names all five with `one each and no repeats`; the video bin binds the composite still at position 1.

**Prompt skeleton A** (two-dancer break-from-work beat; the user's "very good, save this recipe", seq28 vid22 take 27):

```
<lora:crashtest-dance-v2_high_noise:1.0> <lora:Wan-high-waist-dance-V5.safetensors:1.0> dance style tiktok, hip hop. Real-time full dance speed, never slow motion. The bucket-hat man and the box-braids woman stand leaning over their keyboards at the glowing GPU workstation, finishing their work; they trade a grin and EXPLODE into a HIGH-ENERGY full-throttle dance -- fast, powerful, athletic, maximum hype, and both dance AWAY FROM THE TABLE, traveling out into the wide open floor in front of the desk with big confident steps, each claiming their own space on the floor -- they dance side by side facing the camera, never greeting each other, never hugging, just DANCING hard in the open room with the glowing workstation behind them -- big hits, sharp isolations, real physical footwork; the MAN dances with smooth confident SWAGGER, controlled grooves, clean deliberate footwork and natural rhythm in time with the beat, stylish and effortless, never stiff, never awkward, never flailing; big powerful moves with real physical weight and hard hits landing ON the beat -- explosive but always physical, never floating, never hovering, never slow motion; the energy keeps CLIMBING all the way to the last frame, both dancers going full-out at the end, the glowing monitors and RGB cooling fans visible behind them. EXACTLY TWO dancers, nobody else ever appears. Each keeps the same face, hair and clothing the whole clip: he stays in his black bucket hat and sleeveless black hoodie, she stays in her red racerback crop top and box-braids bun. Static locked-off camera, no push-in, full bodies always in frame, one continuous take, no cuts.
```

Negatives: `third person, pink bob, pink hair woman, extra dancer, three dancers, duplicate person, twins, cloned dancer, crowd, bystander, talking, speaking, lip sync, mouth flapping, walking toward camera, camera push-in, zoom, leaving the room, exiting frame, sitting back down, crouching, slow motion, deformed hands, blurry, watermark, mirror, reflection, gender swap, changing identity, clothing changing, awkward dancing, stiff dancing, flailing arms, jerky movement, off-beat, floating, hovering, levitating, gliding, flying, feet off the ground, greeting, handshake, hug, staying stuck behind the desk`

**Prompt skeleton B** (five-dancer aerial finale, seq34 vid25 take 3):

```
<lora:crashtest-dance-v2_high_noise:1.0> <lora:Wan-high-waist-dance-V5.safetensors:1.0> dance style tiktok, hip hop. Real-time full dance speed, never slow motion. The five-member crew dances hard as one, big full-amplitude moves, energy building through the clip. The camera orbits fast around the crew on the hardest hits, powder bursting upward. The choreography keeps ESCALATING into a huge high-flying finale: all five LAUNCH into big synchronized AERIAL JUMPS, explosive airborne leaps with knees tucked and arms flung wide, hanging in the air on the biggest hit, magenta and teal powder erupting beneath them as they fly; the energy peaks at the very END and the last frame catches the whole crew AIRBORNE mid-leap. Every dancer keeps the same face, hair and clothing as the first frame, five distinct people, no duplicates.
```

Crew wide still (Klein, the scale-dummy plate bound as `.warehouse`, seq1 id20):

```
((exactly five dancers and no one else, five different people, each outfit worn by exactly one dancer, no duplicates, no bystander, nobody in the background)) the five-member dance crew in a graffiti-covered warehouse, standing in a loose formation, grooving: a tall Black woman with two afro puffs in a teal puffer vest and silver joggers, a buzz-cut bearded man in a bright orange bomber, a pink-bob woman in a black-and-yellow moto jacket and yellow trousers, a lanky man in a black bucket hat and a silver puffer, and a woman with long box braids in a red bomber and rainbow leggings. wide shot, fierce confident expressions, aggressive metal dance energy, at natural realistic human scale, dramatic colored lighting, sharp focus
```

**Params**: length_s 4 for beats, 5 for the finale; quarter true through review, quarter false for the final regen (quarter WAN cannot be upscaled); fps 16, 14 steps; seed reroll once before any prompt rewrite. Beat map: 120 BPM, beat 0.5 s, 4 s = 8 counts; a beat-count prompt needs at least 4 s.

**What it fixed**: the three clauses that broke recipe A: `dance together` (they walked around the desk to greet each other), `stay planted on their own spots` (cramped desk-pinned dancing), `every jump small` (killed the energy); the working middle is EXPLODE + travel AWAY from the set piece + own space + physical weight. Scale-dummy location plate fixed giant dancers. Roster lock (`one each and no repeats` plus `duplicate person, twins, cloned dancer, two <garment>, missing <distinctive person>`) fixed the dropped-and-cloned dancer. Smoke source rule and powder-with-weight rule: `canisters ... jet thick magenta smoke that rolls across the court` passes; `thin ... thickens steadily` and body-synced bursts read as gas from a body.

**Where it does not apply**: LoRA header, `Real-time full dance speed, never slow motion` and the static-camera clause are WAN wording; probe before sending to `ltx2.5`. Six-dancer negatives reduce but never eliminate extra people; the fix is the count lock at the HEAD of the prompt.

## dance-crew-ugc

**Project**: vertical UGC-style ad for THM with the crew as talking creators (phone in hand, logo on the chest) intercut with dance beats.

**Models then**: image `klein_multi_image.json` (phone and chest-up stills); video LTX-2 i2v with audio (`THM_video_ltx2_i2v.json`, fps 16, img-to-video strength lowered to 0.5) for talking clips, WAN 2.2 14B i2v full size for dance beats; Seedance/Kling bake-off rejected for dialogue.

**Models now**: `klein`; `ltx2.5` for talking clips, which needs a probe for the dialogue clauses (the `Audio:` line, voice description, chest-up requirement, post-line exit) before a batch; `wan2.2_14B` for dance beats.

**Prompt reference now**: `guide ltx2.5` and `guide wan2.2_14B`; the vendor's current LTX-2 prompting guide (audio and dialogue section), fetched with permission.

**Reference setup**: per-creator bins tagged `character` (rehearsal-fit close portraits), a `THM Logo` bin tagged `character` bound as `.logo` where the chest mark must be faithful, studio angle bins tagged `location`, an uploaded app screenshot in its own bin bound as `.app` for the phone screen. The talking video bin binds the chest-up still at position 1 only.

**Prompt skeleton** (talk in place, seq13 vid7):

```
the dancer talks to the camera at a brisk natural pace in place, lips and jaw moving clearly with every word, a small open-hand gesture, easy confident energy, slight handheld sway, the same dance studio the whole clip, the mirrored wall and corner desk staying exactly as they are behind her, the room never changing, she is the only person in the room, no cuts. She finishes the line and settles into a relaxed natural smile, holding easy eye contact until the very end. Audio: a warm, relaxed woman's voice with a soft British English accent, friendly and unhurried, speaking at a natural quick pace, says, 'It goes to work. You check it when you feel like it.'
```

Negatives: `hallway, doors, new room, changing room, room morphing, outfit changing, jerky motion, teleporting limbs, second dancer, another person, extra person, person in mirror, cut, awkward frozen pose at the end, shiny skin, glossy skin, slow motion, closed mouth, frozen face, static lips, blurry, watermark, deformed hands`

Chest-up start still (Klein, seq13 id42):

```
chest-up medium close-up, vertical framing, a tall Black woman dancer with two high afro puffs and silver hoop earrings (.nova), wearing her fitted teal ribbed crop tank with the large orange square logo with a bold white H, talking to the camera mid-word, relaxed confident expression, holding her phone loosely in one hand at chest height, the other arm relaxed at her side, the floor-to-ceiling mirrored wall of the fantasy dance studio behind her reflecting the open floor (.studio), violet and amber glow, sharp focus on her face, the mirror behind her reflects only the empty studio floor and her own back, nobody else anywhere, matte natural skin with soft even texture, no shine
```

Walk-away sign-off (seq17 vid9): `the man slides his phone into his jacket pocket, finishes his line, then turns and walks OUT of the frame to the side, leaving an empty rooftop, one continuous take, no cuts. Audio: a warm, easygoing man's voice with a light New York accent, casual and laid-back, speaking at a natural quick pace, says, 'Back in my pocket. The machine keeps working for me.'`

Klein phone grip (seq7 id65 still + vid3): still `holding her smartphone up in one hand turned SCREEN-SIDE FLAT toward the camera so only the phone screen is visible, the screen showing the app interface (.app)`; negatives `phone back, rear camera, camera lenses, camera bump, back of phone, selfie view, first-person POV, disembodied hand, viewer's hand, palm facing camera`; motion `she smoothly EXTENDS her arm and PUSHES the phone forward toward the camera in one continuous confident gesture, the phone growing until it FILLS MOST OF THE FRAME in a sharp close-up by the final frame, screen flat toward the viewer, the back of her hand and fingertips gripping the edges`.

**Params**: talking clips length_s 4-5, full size (LTX is cheap at full res); dance beats length_s 4-5 on WAN, quarter for drafts; fps 16 on the LTX-2 graph then, 24 on `ltx2.5` (whole seconds land on the 8n+1 grid); the same voice string reused verbatim per speaker across every clip.

**What it fixed**: chest-up start frames made lip-sync read as real; identical voice descriptions stopped per-clip accent and gender changes; talk-in-place and walk-away were the only reliable stagings (talk-while-approaching inverted to a walk away, and the FLF graph did not pin the end frame on that install); ending as a target expression (`presses her LIPS TOGETHER into a cool, knowing SMIRK`) beat three seeds of `NO laugh`; trailing dead air (1-2 s per clip) is trimmed at speech end + 0.45 s; walk-and-talk slow-mo is fixed in post with a 1.2x pitch-preserving remap.

**Where it does not apply**: `Audio:` lines mean nothing on WAN or H3; Klein phone grip is a coin flip per take and must be QC'd every time; audio and lip-sync cannot be verified by the agent, so no talking take is marked pass on that axis.

## ugc-skincare / ugc-lotion

**Project**: UGC product ads: a creator, a hero lotion/skincare product, a store shelf and corner, then talking clips with the product to camera.

**Models then**: SDXL (Analog Madness) for creator, product and location plates; `klein_multi_image.json` composites; LTX-2 i2v with audio for talking clips.

**Models now**: `sdxl_analog_madness` or `krea2_turbo` for plates; `klein` for composites; `ltx2.5` for talking clips (probe the dialogue clauses as in dance-crew-ugc).

**Prompt reference now**: `guide klein` and `guide ltx2.5`; the vendor's current FLUX Klein editing guide, fetched with permission.

**Reference setup**: staged pinning, one bin per stage, each bin's selected media bound into the next: a `product-shape` bin (explore pump / tube / jar with no references, spread seeds) -> a `product-color` bin binding the chosen shape and varying only the color clause -> a `product-label` bin binding the chosen color and trying 2-3 text treatments. The winner is the bin tagged `character` whose selected media is bound as `.lotion` in every composite. Creator: identity bin (no references, spread seeds) -> outfit bin (binds the face, varies the outfit clause) -> pose bins (full body, several angles). Location: archetype bin -> angle bins (corner, straight-on shelf, down-aisle) each tagged `location`, and a placeholder-marker plate with a red can at the product's spot.

**Prompt skeleton** (composite still with spatial blocking and inline exclusions):

```
medium shot, woman shopper .mara stands (directly in front of the shelf .shelf) which contains several rows of product .lotion, right hand holding one bottle of .lotion at chest height with the label facing the camera, left arm relaxed at her side, left arm must NOT reach toward camera, no left hand at bottom-right corner, no second hand, matte natural skin finish, sharp focus on the label
```

Placeholder-marker composite: `the bottle .lotion standing on the counter positioned exactly where the red can was (the red can is removed and completely gone), loose close-up centered on the marker`.

Product plate negatives: `gibberish text, nonsense letters, illegible logo, blurry text, real brand names, wrong bottle type`. Creator skin: positive `matte natural skin finish`, negatives `oily skin, skin shine, dewy highlight, specular highlight`. Location plate negatives: `people, character, face, hands, shoppers` plus `empty environment, no people` in the positive.

**Params**: image only for the asset phase; seeds spread (10000, 20000, 30000) for explore, spread again for iterate; do not move to video until the user says so.

**What it fixed**: one axis per bin (shape, then color, then label) instead of a kitchen-sink product prompt; the red-can marker fixed framing, position and scale of the composited product in one take; inline `must NOT` clauses fixed a positionally specific phantom hand that the negative field alone did not; the product's own prompt describes only the object (`ceramic teapot, curved spout, rounded handle, warm glazed finish`) and placement lives in the composite prompt.

**Where it does not apply**: readable labels are never guaranteed; a clean unbranded product plate does not prevent gibberish appearing on the product in motion, so keep product beats static and tight.

## crash-test-dinos

**Project**: two racing dinosaurs (green spinosaurus, blue t-rex) sprint through a pedestrian city, smashing obstacles, to a finish line.

**Models then**: image `klein_multi_image.json` composites over SDXL plates (character LoRAs `SDXL-LORA-D1N0_Spinosaurus:0.5` etc. with the `D1N0` trigger and `((human for scale)) adult man stands next to him`; location plates with `SDXL-LORA-urban_environment:0.5`); video WAN 2.2 14B i2v (`i2v_base.json`, fps 16, quarter) with the movement LoRA `dinocity-dinomovement_000002750_low_noise:1.0` on the style layer and the crash LoRA `dinocity-crash_000002750_low_noise:1.0` on the one impact clip.

**Models now**: `klein` and `wan2.2_14B` transfer; `sdxl_analog_madness` for the LoRA plates. The LoRA clauses are WAN-only.

**Prompt reference now**: `guide wan2.2_14B`; the vendor's current WAN 2.2 prompting guide, fetched with permission.

**Reference setup**: one bin per dinosaur tagged `character` whose selected media is the full-scale plate with a human beside it; one bin per street tagged `location` (real plates, `match the pinned location reference image`); each still bin binds location + both dinos; a video bin binds the before-state still at position 1 (obstacle intact) and, for a closed crash beat, the aftermath still at position 2.

**Prompt skeleton** (rear tracking start still, seq5 id5):

```
low tracking three-quarter rear view close behind the two dinosaurs at full sprint down the shopping district street .shopping, the green spinosaurus .spino and the blue tyrannosaur .rex side by side seen from behind and slightly to the side, both facing away to the right down the street, both with hind legs extended in a full running stride and bodies pitched forward, thick dust clouds kicking up from their feet, motion blur on their legs and tails, storefronts on both sides, spectators filming from the curb, no vehicles
```

Negatives: `standing still, walking, calm pose, facing camera, nose to nose, facing each other, no dust`

Crash clip (seq7 vid7, before-state still at position 1, aftermath still at position 2):

```
the camera tracks the green spinosaurus and the blue tyrannosaur running side by side and parallel, not leaning or veering into each other, at full high racing speed straight to the right toward the intact self-serve wooden fruit-crate stand piled with apples and melons ahead, their thick hind legs pumping up and down in a full running stride, each foot striking the pavement in turn with heavy footfalls that kick up dust, weight landing solidly on the ground every step, tails swinging for balance, heads and necks bobbing with each stride, no sliding no floating no flying. the blue tyrannosaur springs up and clears the stand, sailing over it and pulling ahead to the right. <lora:dinocity-crash_000002750_low_noise.safetensors:1.0> at the same instant the green spinosaurus slams head-on into the stand and it EXPLODES apart - the wooden crates shatter into flying planks and the apples and melons burst outward in every direction. the green spinosaurus crashes straight through the wreckage and keeps scrambling to the right after the blue. the camera tracks straight to the right the whole time. the obstacle is destroyed and stays destroyed - it never reassembles.
```

Negatives: `obstacle reassembling, debris flying back together, stand rebuilding, reversing, camera spinning, camera swirling, slow motion, bullet time, two heads, second head, extra head, duplicate head, deformed, both crashing, flying, sliding, standing still`

Anti-slow-mo prefix for every impact clip, verbatim: `Real-time maximum sprint speed entire clip -- camera and dinosaurs stay at full racing velocity through the impact frame, never slow motion, never bullet time, never speed ramp or deceleration into impact, collision at full sprint not slow-mo.` Project-wide video negatives: `cars, vehicles, traffic, dinosaur exiting frame, subject walks off screen, empty frame without dinosaurs, human mirroring dinosaur gait, slow motion, bullet time, speed ramp, trot, human injured, blood, gore, harmed person`.

**Params**: length_s 3-4 for sprints, 4-6 for crash and finish beats (never 2 on a start-only clip); quarter true for drafts; fps 16; crash LoRA at 1.0 on the impact bin only (0.5 for the finish tape). QC frames at 0/25/50/75/100 percent plus late frames at 82/90/97 percent where the impact lives.

**What it fixed**: lane choreography (`subject A on LEFT, subject B on RIGHT, snouts forward, same race direction, never facing each other`) and forward-only tracking (`never reverses, no backward travel`); the low three-quarter rear tracking view made facing-each-other impossible and read as speed; per-limb ground contact stopped gliding; the still at the before-state with the impact in the final 20-30 percent; camera-versus-subject wording fixed a pull-back that read as the dinos walking away. Anti-pattern checklist checked on every take: backward run or moonwalk, moving obstacle, prop morphing, slow trot or deceleration, missing crash burst, screen-direction flip, wrong character count, still-to-video cut mismatch.

**Where it does not apply**: the crash LoRA and the anti-slow-mo prefix are WAN clauses; a rotated end still cannot be conditioned into a rotation on any model; the same prefix on a talking clip is noise.

## settleman-law-ad

**Project**: satirical personal-injury lawyer ad: a lawyer talking to camera in an office, walk-ins and stunts, on custom LTX-2 and Klein workflows.

**Models then**: image `klein_multi_image.json`; video LTX-2 i2v with audio (custom graph; static i2v traced to the img-to-video conditioning strength at 1.0, fixed at 0.5).

**Models now**: `klein`; `ltx2.5`.

**Prompt reference now**: `guide ltx2.5`; the vendor's current LTX-2 prompting guide, fetched with permission.

**Reference setup**: a lawyer bin tagged `character` (hero portrait, no LoRA) bound in every still; an office bin tagged `location`; each talking video bin binds a chest-up still at position 1; a stunt bin binds a still at the before-state (the fall not yet begun).

**Prompt skeleton** (clauses that carried, from the guide's worked examples):

```
[talk-in-place] the lawyer stays fully in frame the entire clip, faces the camera and talks directly into it, mouth moving clearly in sync with his words, small confident hand gestures, the office behind him never changing, no cuts. Audio: a booming, over-earnest man's voice with a mid-Atlantic accent, speaking at a brisk pace, says, '<line>'
[lighting gag, literal] brief dim-bright flicker in the overhead light, no flame, no fire
[approach, literal distance] he strides toward the camera, each stride visibly advancing him closer to the camera
[stunt, single axis + end state] he stumbles straight forward and falls flat on the carpet, landing face down with both arms out by the middle of the clip, and stays down
```

**Params**: length_s 4-5 for lines; full size; fps matched to the workflow; the same voice string per speaker in every clip.

**What it fixed**: identical voice description per clip; never mention text in any field (the negative `text on clothing` made the gibberish persist, the run with no text mention cleared); metaphors read literally (`like an aging flash unit` produced fire, `nodding-donkey` produced a horse); compound rotations (flip, inversion) distorted anatomy and never landed, single-axis falls with an explicit end state did; static i2v was a workflow-strength matter, reported to the user rather than rerolled.

**Where it does not apply**: the strength fix lived inside the 1.x graph; on `ltx2.5` a static take is reported as a workflow matter, not patched by the agent. Audio cannot be QC'd by the agent.

## one-straw

**Project**: a wordless 53-clip industrial film following one red plastic straw from oil well to diner table, all machinery and trucks, no visible faces.

**Models then**: image `klein_multi_image.json` on real photo plates; video `THM-video_ltx2_5_flf2v.json` (LTX 2.5 first/last frame, fps 25, full size).

**Models now**: `ltx2.5` transfers directly; `klein` for stills.

**Prompt reference now**: `guide ltx2.5`; the vendor's current LTX-2 prompting guide, fetched with permission.

**Reference setup**: one bin per machine or truck tagged `character` (real photographs, e.g. the pump-jack photo that built the approved bookend), one bin per factory station tagged `location`, a `Hero Straw` bin whose selected media is a scale-anchored plate (a dinner fork beside the straw); every still bin binds the location and the relevant vehicle, and the video bin binds the still at position 1 (position 2 only when both frames share every element).

**Prompt skeleton** (state persistence, seq19 vid19):

```
ONE single solid red drinking straw lies alone in the cradle of a straw wrapping machine as a clear film wrapper closes around that one straw and is heat-sealed at both ends, and the finished individually-wrapped straw is carried slowly away to the right. Throughout the entire shot there is only ever ONE straw in the frame - no other straws appear, nothing multiplies, no second or third straw enters. The camera holds locked off in close-up, framing unchanged. Cool factory light, glossy red plastic under crisp clear film, brushed steel, shallow depth of field. Reels ticking and unspooling, close and loud in the foreground; a heat sealer thumping in steady rhythm, clear and present at working distance; the rhythmic clatter of a packaging line, clear and present at working distance. There is no music of any kind anywhere in the shot.
```

Sealed carton (seq20): `The box STAYS COMPLETELY SEALED for the entire shot - it never opens, the flaps never lift, and NO straws are ever visible anywhere in the frame because everything is inside the closed carton.` Forklift (reframed to forks only): `The forks stay in frame in the lower left for the entire shot and the pallet stays solid and intact.` with negatives `forks disappearing, vanishing forklift, empty frame, pallet disappearing, truck driving away, wide shot`.

Standard negatives: `music, musical score, soundtrack, song, singing, melody, instrumental, background music, spoken dialogue, intelligible speech, narration, voiceover, on-screen text, watermark, subtitles, logos, letterboxing, black bars, flicker, frozen still frame, cartoon, oversaturated, people, humans, figures entering frame, crowd` plus, on any single-framing clip, `cut, dissolve, cross-fade, scene change, jump cut, new angle, second shot, montage`.

**Params**: length_s 3-4; full size; fps 25 on that graph (only 8.00 s is a clean whole second at 25 fps; use 24 fps on `ltx2.5` for whole-second lengths); audio motifs as constant strings with distance qualifiers.

**What it fixed**: persistence clauses stopped straws multiplying, cartons unsealing, forklifts vanishing and lids closing on their own; a wrong start frame cannot be rescued by the video prompt (a clip kept dissolving until the still itself was regenerated); 15 of 49 stills had empty reference lists and mapped exactly onto the "it keeps changing" complaints, and the purpose-built machine plates already existed unbound; scale came from the reference's apparent size (macro straw plate = fence-post straw), fixed by a scale-anchored plate and comparative language; two failed persistence attempts meant reframe (forks only, hands only).

**Where it does not apply**: the wide `eyes cropped above the frame` rule is unsatisfiable on wides; cut below the faces. A mechanical sweep (duration, audio level) proves playable, not good; vision QC in story order still applies.

## van-halen-movie

**Project**: 2.x from the start: recast a 1982 Van Halen concert clip with actors (Klein stills), then re-perform it as a modern-looking clip on MiniMax H3.

**Models then**: image `klein.json`; video `minimax_h3_r2v.json` and `minimax_h3_r2v_enhancer.json`; 1280x720.

**Models now**: same: `klein` stills, `minimax_h3_r2v_enhancer` video.

**Prompt reference now**: `guide minimax_h3_r2v_enhancer` for the baked enhancer prompt (the structured form is mandatory, including the closing negatives line); the vendor's current MiniMax Hailuo prompting guide, fetched with permission.

**Reference setup**: source frames in upload bins (`ed-og`, `mike-og`, `dave-og`, `band-og`); actor headshots in their own bins (`jgl`, `keanu`, `justin`, `untitled`); casting bins (`Ed`, `Mike`, `Dave`) on `klein` binding the source frame plus one actor reference each, costume from text; the video bin binds the close casting portraits (`.mike`, `.mike.2`, `.ed`, `.dave`) and the source clip `.band-og`, and unbinds the wide band still that had leaked its frozen pose.

**Prompt skeleton** (Video 6, the passing take-22/26 structure, trimmed):

```
subject_definitions:
<Subject 1> (.mike, .mike.2): the bassist, center: the man in .mike and .mike.2, the same person, round face, clean-shaven with only light stubble, no beard, thick dark curly hair, heavy brows, dark eyes, stocky build. From the stills only the face, hair, build and his plain sunburst solid-body electric bass with a maple neck; not the green vest, not the black t-shirt, not the headphones, not the studio, not the stills' stage lighting. He wears a sleeveless maroon tank top with gold embroidery and white flared trousers.
<Subject 2> (.ed): the guitarist, right: the man in .ed, lean face, strong jaw, straight nose, dark eyes, thick dark brows, clean-shaven, long black wavy hair past his shoulders. From .ed only the face and hair; not the dark jacket, not the wood-panel room, not the pointing pose, not the daylight. He wears a blue-and-white striped shirt open to the waist and red flared trousers. His guitar is strictly this and nothing else: a white solid-body electric guitar with a double-cutaway body, the whole body covered in bold black diagonal tape stripes, a plain maple neck, single-coil pickups, no pickguard. It stays that exact guitar in every frame.
<Video 1> (.band-og): a performance and camera reference only: a handheld concert clip in which the bassist, bass slung low at his hips, thrusts his right arm straight out and points his index finger into the lens while shouting with his mouth wide open, then drops the arm and his right hand goes back to plucking over the pickups; the guitarist lifts his picking hand off the strings, raises it to head height with the fingers spread in a claw, palm to the camera, mouth open, leaning forward, then bends over the guitar; and the singer bursts in with the microphone in his right hand, eyes wide, mouth open, until his face fills the frame, then whips his head away; the camera pushes in and whips between them. Nothing visible in the clip is reused as pixels: not its people, not their faces, not their clothes, not its stage, not its colors, not its low-resolution vintage look.
<Subject 3> (.dave): the singer, off camera until the end of the shot: the man in .dave, long wavy blond hair past his shoulders, clean-shaven, blue eyes, lean bare torso. From .dave only the face, hair and build; not the black leather jacket, not the arena and crowd behind him. He wears a shiny silver shirt open to the waist over a bare chest with a studded belt and black-and-white striped spandex trousers, and holds a microphone.

summary:
[reference generation] <Subject 1> and <Subject 2> open the shot as a two-man front line on a concert stage, playing hard at a restless handheld camera, and <Subject 3> enters only at the end, lunging into the lens from off camera; there is exactly one singer, in sharp, high-resolution, modern-looking footage, re-performing the moves, hand positions, finger motion, body positions, facial expressions, pacing and camera of <Video 1> beat for beat as described below, with the playing continuing before and after those beats to fill the longer shot. <Video 1> is not a frame, not an edit source and not a source of appearance.

retention_analysis:
<Subject 1> (throughout): fully_preserved - the bassist's clean-shaven facial identity, hair, build and plain sunburst bass from .mike and .mike.2 are retained in every frame, including when he shouts; the stills' vest, t-shirt, headphones, studio and lighting are not retained. No beard.
<Subject 2> (throughout): fully_preserved - the guitarist's facial identity and hair from .ed are retained in every frame, in motion and in profile, and the white black-striped guitar is retained exactly in every frame; the still's jacket, room, pose and daylight are not retained.
<Subject 3> (end of shot only): fully_preserved - the singer's facial identity and hair from .dave are retained from the moment he enters, including when his face fills the frame; he does not appear before that and there is never more than one of him; the black leather jacket and the arena of the .dave still are not retained.
<Video 1> (throughout): weak_reference - only the sequence of moves, the pacing and the handheld camera are retained, re-performed by <Subject 3>, <Subject 1> and <Subject 2>; the people, faces, wardrobe, stage, colors, framing as pixels and vintage low-resolution look of the clip are not retained. Where <Video 1> and the subjects differ in any visible way, the subjects win.

detailed_description:
A sharp, high-resolution, modern-looking concert shot: a stage lit by a bright white front key light with warm natural skin tones, a plain black backdrop, a drum kit far behind, no colored wash. [Shot 1] A handheld camera, low and close, already moving, on the band mid-song with only two performers in frame, both in full motion, bodies bouncing hard on every beat, heads banging, hair swinging, never planted: <Subject 1> the bassist at center-left and <Subject 2> the guitarist at right, with empty stage on the left where the singer is not yet. The camera starts tight on <Subject 1> ... then he thrusts his right arm straight out and jabs his index finger into the lens, mouth wide open in a shout ... The camera whips right to <Subject 2> ... lifts the picking hand off the strings and raises it to head height, fingers spread wide in a claw ... The camera pushes in tight on the fretboard and both hands fill the frame for the solo: his left hand high on the neck, thumb behind it, index, middle, ring and little fingers each pressing their own string on their own fret in turn ... Then the camera whips left as <Subject 3> the singer bursts into frame from off camera on the left ... lunges at the lens until only his face fills the frame, eyes wide, mouth open mid-yell, for one quick beat only, then whips his hair and his head away and springs back, and the camera pulls back with him to a low, close, tilted angle on all three now in frame together for the first time, still moving hard ... Every performer keeps moving for the whole duration; nobody stands still, nobody holds a pose; the camera never stops moving. The wardrobe and the stage lighting stay exactly as described for the whole shot ... the light stays bright white from the first frame to the last with no colored wash coming up. Each face stays sharply resolved, solidly attached to its head, and unmistakably the person from its still at every distance and angle. No singer in the opening frames, no second singer, no duplicate of any performer, no beard on the bassist, no other guitar on the guitarist, no plain white guitar, no guitar without the black stripes, no faces from the clip, no standing in place, no long hold on any one face, no wardrobe changes, no lighting changes, no red wash, no colored stage lights, no haze, no locked-off camera, no static wide shot, no thumbs-up, no bottle-shaped bass, no leather jacket, no posed portrait smiles, no face drift, no extra people, no vintage broadcast look, no low resolution, no color bleed, no video noise, no static performers, no frozen frames, no added cuts, no subtitles, no watermarks, no on-screen or burned-in text.

overall_soundscape:
[soundscape not specified]

non_diegetic_music:
[music not specified]
```

**Params**: ladder quarter 3 s -> quarter 5 s -> full size 5 s (1280x720) -> full size 8 s for the fretboard push-in; a full-size take is a new render and gets full QC (12 sampled frames, identity per frame); length_s must not equal the bound clip's length (3 s = the 2.9 s clip replayed with the original faces).

**What it fixed**: actor casting in Klein with one actor reference per source frame and costume from text (`skin tone and coloring retained from .mike-og, nose preserved ... like a very well done lifelike prosthetic`); the description-only round (no clip bound, the band's wardrobe written out) was the first time all three faces read as their stills; the clip re-bound as `weak_reference` with every beat written out re-performed the source without replaying it; a 6 s single-subject clip (`Eddie in Japan`) outweighed any still until it was unbound and the solo written from the reference; the close casting portrait replaced the wide take as `.mike` and finally gave Mike a face; the white key described positively killed the red wash; the fretboard push-in with per-finger description made the solo real; the quarter -> longer -> full-size ladder proved structure before spending full renders.

**Where it does not apply**: two-hand tapping never rendered in two passes; a bound clip drifts back into its own pixels after its length runs out (trim the tail or write the extra beats); `subject_definitions` is an H3 form only, never sent to WAN or LTX.

## memes (newtoold: the Matt Damon grows-old morph)

**Project**: recreate the "Matt Damon grows old" meme: the young soldier from the film ages in one locked-off shot in the film's own 90s crossfade-and-warp style, but lands on the real older, bearded Matt instead of the film's older actor. Silent, 5 s, 1280x720.

**Models then**: video `minimax_h3_r2v_enhancer.json`; the old-Matt end still from `klein.json` (a portrait of the bearded Matt composited over the young frame).

**Reference setup that worked** (bin `newtoold`, take 10): `.youngmatt` = first frame of the source clip (position 1), `.oldmatt` = the Klein end still (position 2), and the source clip **trimmed to its first 1.72 s** (ffmpeg `-t 1.72 -an`, uploaded with `--from` the full clip, cut before the film's older actor is recognizable) bound as `<Video 1>` weak_reference for morph style, pace and grain only. The user asked for exactly this: MiniMax because it can reference the original; weaken the previous actor.

**What failed first, so nobody repeats it**:
- The full clip bound (takes 1-3): replays its second half, the film's older actor appears at 2.9 s; declaring it weak_reference with every face excluded changed nothing on a fixed seed.
- Stills only, no clip (takes 4-9): the older actor is gone but H3 ages feature by feature (hair greys, then lines, then stubble, then beard, shirt last) on every seed and every wording, including a timed per-second clause and a "every feature at the same rate" clause. The user rejected it: "it happens in parts".
- A Klein midpoint keyframe (bin `midmatt`, 6 takes): Klein re-composed the frame and swapped the face for another man every time, with two references or one.
- LTX 2.5 with start and end frames (bin `newtoold-ltx`): uniform face morph with a translucent halo on the hair edge and the shirt lagging (fixed by tying the shirt to the face in the prose); the user ruled LTX out for this bin.

**Prompt skeleton** (H3 six-section form; `.youngmatt.2` is the trimmed clip's handle):

```
<Video 1> (.youngmatt.2) - the opening of the original film clip: the young man holding still as a slow crossfade-and-warp morph begins to age him. Used only for the style, pace and film grain of that morph and the locked framing. Not a source of any face other than the young man's, and the clip ends before it reaches any older face.
summary: [keyframe completion] + [reference generation] A single static shot morphs from <Picture 1> to <Picture 2> in exactly the style of the morph that begins in <Video 1>: one continuous crossfade-and-warp in which the whole face changes together, carried past the end of the clip all the way to <Subject 2>.
retention_analysis: <Video 1> (whole clip): weak_reference - only the style of the morph (a smooth crossfade-and-warp where every feature changes at once), its slow even pace, the locked static camera and the 35mm film grain are retained; excluded: its pixels as a source, its wardrobe and any face other than the young man's; the older face the original film morphs into never appears.
detailed_description: ... His face begins to morph exactly as it does in <Video 1> ... and that same morph continues in the same style past where the clip ends until it arrives at <Subject 2>. Hair, skin, jaw, beard and shirt all move together from young to old in the same frames ... there is no third face and no detour through any other older man. ... No face from the original film other than the young man's, no feature changing before another, no sudden jump, no camera movement, no added cuts, no subtitles, no watermarks, no on-screen or burned-in text.
overall_soundscape: Silent. No ambience, no physical sound, no audio track at all.   non_diegetic_music: N/A
```

**Params**: quarter to prove, seed 25739836 held across every take so one axis at a time could be judged, then full size on the same seed; a full-size 5 s H3 render runs past 10 minutes. QC video at 4 fps over the whole clip and 8 fps over the transition, cropped to the head.

**What it fixed**: the wrong older face (cut him out of the reference clip instead of writing him out of the prompt); the feature-by-feature aging (the clip's own morph style, copied as weak_reference, gives the crossfade-and-warp where everything changes together; no stills-only prompt achieved it).

**Where it does not apply**: a bound clip's second half will replay no matter what the text says, so trim, do not describe; a young hold of about 1.75 s and an old hold of about 2.25 s remain, like the original; audio was never judged.
