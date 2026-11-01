# Audio tool: QC a take's sound, read a song's structure, check an export against the beat

Run it only through the CLI, which fetches media ids and sets the environment:

```
python thm-agent/cli.py --project <slug> tool audio <verb> <media id | path> [args] [--fps N] [--scratch DIR]
```

Every verb prints one JSON object. Times are seconds; with `--fps N` every time also comes as a
whole frame (`start_frame`, `cut_frame`, ...). Every verb reads the audio track of a video as
readily as an audio file. The tool measures and writes nothing to the project: a finding becomes
a `mark_take` note, a correction becomes a re-render, a mix change becomes `set_item_mix`.

CPU only, in its own venv. First use of a verb downloads its model into `thm-tools/audio/models/`
(whisper small ~460 MB, medium ~1.5 GB, ECAPA ~80 MB, beat_this ~80 MB).

## What the tool cannot judge

Sarcasm, warmth, naturalness, whether a pause is "heavy", whether an accent is the one the user
wants, lip sync. Those stay a "please listen" line in the QC note. The tool tells you the words,
the speaker, the stressed word, the levels, the beats and the cuts; it does not tell you whether
the read is good.

## QC verbs

### `transcribe <media> [--expect "line"] [--model small|medium] [--language en]`

Words with `start_s`, `end_s`, `prob`, the whole `text`, the detected language. With `--expect`:
`wer` (word error rate after lowercasing and stripping punctuation), `match`, and `diff`, a list
of `{op: missing|extra|substituted, expected, heard}`. `low_confidence` lists words under 0.5
probability: whisper turns garbled speech into plausible words, so a low-probability word that
"matches" is not proof the word was spoken.

- **Default model `small`** (calibration, 2026-10-18, CPU, model load included): 6.4-9.4 s per
  3-14 s take. `medium`: 16-24 s per take, over the 15 s budget, so it is the second opinion:
  run `--model medium` when small reports a diff or a low-confidence word on a take that
  otherwise passes, before you fail it for the words.
- Pass the line **from the take's own recipe** (`recipe <media>`), never the bin's current
  prompt: on online-gaming the Get broker and quiet-kids lines were rewritten after the early
  takes, and against the current prompt those takes read as 90-100% WER with nothing wrong.
- `wer == 0` is "line correct". `wer <= 0.15` with only a contraction or filler difference
  (`dunno` heard as `don't know`, a stray `d`, "All right," before the line) is still the line;
  say so in the note. Above that, read `diff`: a run of `extra` words is the model speaking
  direction text or gibberish (the quiet kids take 51: 10 extra words, WER 0.43; Get broker 46:
  WER 2.25, six low-confidence words); `missing` words are a dropped line (kids like me 6 said
  "didn't really like to socialize" for "Shy"). Of the calibration takes, small and medium
  agreed on every verdict except two where medium was right (the missed "Wait" on the screen 35,
  the stray "D." on double order 16) and one where small was right (swatted hand 69, where
  medium added "All right").
- Hallucination on non-speech: whisper writes "Thanks for watching" on a bare tone. Check
  `levels` first when a clip may have no speech.

### `voice <media> --ref <media>[,<media>...] [--window s0:s1]`

ECAPA cosine similarity of the line (or the window) to each reference voice, `best` and `margin`
(best minus runner-up). Stateless: pass the character's reference takes every call; which take is
a character's reference voice is a project rule (`add_project_rule kind=continuity`) or a bin tag,
not the tool's business.

- **Thresholds** (calibration on online-gaming, 2026-10-18; Tommy refs = quiet kids take 5 and
  smart kids take 6, Dev refs = Get broker takes 12 and 13, one speaker each):
  - **>= 0.75: the same voice.** Takes of the same bin lineage against their own reference:
    0.84-0.91 (quiet kids 46/51/52 vs take 5).
  - **0.50-0.75: the same character, another render's voice.** Monologue takes from other bins
    against the Tommy refs: 0.57-0.79. This is the drift the user hears as "the accent and
    timbre are inconsistent"; the two Dev refs score only 0.38 against each other.
  - **< 0.45: a different voice.** Dev's takes against Tommy: 0.14-0.49; the "sounds British"
    double order take 16: 0.19-0.26 against everything. Non-speech (room tone): about 0.
  - **Margin >= 0.10** to the other character is what names the speaker; the wrong-speaker take
    (the screen 35, Dev saying Tommy's "Wait. That's it?") reads Dev 0.48 vs Tommy 0.34 on the
    whole clip while the words were right, so `transcribe` alone would have passed it. Run both
    on dialogue.
- **Scores are relative, and the register matters.** The double order takes the user accepted
  with "voice from Tommy wins" score only 0.30-0.36 against Tommy wins itself: a shouted
  reference against a spoken line reads as a stranger. Use a calm spoken line of the character
  as the reference (the take the user approved for the voice), compare the candidates of a bin
  against each other as well as against it, and report the numbers, not a verdict, when the
  best score sits in the 0.50-0.75 band.
- **Window the line.** On a two-shot both voices are in the clip and the whole-clip score is
  ambiguous (the screen take 13: Tommy 0.41, Dev 0.43). Take `start_s`/`end_s` of the line from
  `transcribe` and pass `--window`.
- A reference should be one speaker alone; a reference scores 1.0 against itself.

### `emphasis <media> --expect "you know what YOU can do"`

Aligns the heard words to the written ones, measures each word's pitch peak, intensity peak and
length (Parselmouth), and gives a `prominence` per word (z-scores: 0.4 pitch + 0.4 intensity +
0.2 length). The CAPS words in `--expect` are the intended stress (`intended`); `most_prominent`,
`stress_matches`, `margin` and a `verdict` say where the stress landed.

- Write stressed words in CAPS in every dialogue prompt (filmmaking.md); without a CAPS word the
  verb only reports prominence.
- Calibration: Tommy wins take 10 ("No way! NO WAY!") lands the stress on the second `way`,
  margin 0.82, as written. Good presents take 20 ("...could LEARN to...") put the top word on
  `buy`, but that take has extra spoken bits the user already flagged and the transcript carried
  words that are not in the line, so the alignment was off: **run `transcribe --expect` first
  and only trust `emphasis` on a take whose words match.**
- A `margin` under 0.5 is a close call ("not because" 34: `because` 0.24 ahead of
  `recognition`): say "stress is shared between X and Y" rather than calling it wrong. Over 0.8
  the ear will agree.
- If the intended word was not heard at all, the verdict says so: that is a `transcribe`
  finding first.

### `levels <media>`

`peak_dbfs`, voice-band (300-3400 Hz) loudness per 0.25 s window (`windows`), `voice_p90_db`,
`voice_median_db`, `clicks` (sample jumps of 0.2 full scale and 12x the local motion, with the
level before and after), `edges` (first and last 0.3 s against the body's loudest windows) and
`flags`.

- Generated dialogue on this instance peaks at 0.8-3.0 dBFS after decoding (22 of 22 takes) and
  sits at a voice P90 of -8.5 to -19.5 dB. The export's limiter handles the peaks; only `hot`
  (above 4 dBFS) is a finding.
- A flag is a reason to listen at that time, not a verdict. No flags and a P90 in the usual band
  means "levels fine" in the note.
- `levels` is a dialogue measure. On a music-led preview or export the first downbeat trips
  `loud head` (the kick is louder than the 0.25 s windows after it); read the song with `beats`
  and `energy`, and keep `levels` for the voice.
- Known miss: "things I cared about" take 24 (extra gibberish bits the user heard) transcribed
  clean (WER 0) and raised no flag; the words landed between the lines. Count the speech
  windows: a 6.6 s take whose `windows` show voice-band energy well past the length of the
  written line has more in it than the line. Gibberish that whisper does hear shows as many
  `low_confidence` words (room tone take 2: 11-13 of them on no written line).

## Build and compliance verbs

### `beats <media> --fps N`

`tempo_bpm`, `beats_per_bar`, `beat_times_s`, `downbeat_times_s`, `bars` (each with
`start_frame`, `frames`), `phrases` for 1, 2, 4 and 8 bars (each with `start_frame`, `frames`),
`bar_frames_typical`, `phrase_frames_typical`. Frame counts come from cumulative frame positions
(`round(t * fps)` of each boundary), never from summed rounded seconds: at 23.976 fps a 2 s bar
is 48 frames most of the time and 47 now and then, and the sum is exact.

- beat_this (CPU) takes about 6 s on a 30 s song. Exact on a steady synthetic track; on a real
  song trust the downbeats less than the beats when `beats_per_bar` is not what you hear.
- Plan shots on phrase boundaries: a shot's `length_s` is the phrase plus a small handle each
  end, and its trim brings it to exactly the phrase's `frames`.

### `energy <media> [--fps N] [--beats beats.json]`

Per bar: `intensity` 0..1 (0.5 loudness + 0.25 spectral centroid + 0.25 onset density, each
scaled between the song's 5th and 95th percentiles, smoothed over 3 bars), plus the raw figures.
`sections` from a checkerboard novelty over bar timbre and harmony, each with `bar`, `bars`,
`start_s`/`end_s` (`start_frame`, `frames`), `intensity` and a `level`: `still` < 0.15,
`calm` < 0.35, `building` < 0.6 (or a calm section that rises 0.1 over the one before), `high`
< 0.85, `peak` above. Pass `--beats` the JSON from `beats` to skip tracking the song twice.

### `cuts <export> --song <media> --song-in s --fps N [--start-frame F] [--threshold 0.12]`

Scene cuts in the export (mean grey change between neighbouring frames at least `threshold` and
4x the clip's usual motion; frame-exact, decoded in passthrough so nothing is duplicated), each
with `cut_frame`, the nearest `beat_frame` and `downbeat_frame`, and the miss in frames. The song
is placed as the layer is: `--song-in` is `source_in_s`, `--start-frame` the layer's
`start_frame`. `on_beat` counts cuts within one frame; `off_beat` lists the rest;
`max_miss_frames` is the headline.

- A dissolve or a very dark cut can fall under the threshold; a fast pan can cross it. Read the
  list against `sequence show` before reporting a miss.

### `motion <video>`

Mean frame-to-frame grey change (0..1) per second and overall (`motion_mean`, `motion_p90`), at
64x36. A rough action level: a locked-off talking head reads around 0.002-0.01, a whip pan or a
cut above 0.1. Compare a shot's `motion_mean` against its section's `energy.level`: a `peak`
section over a shot that measures like a still is the disagreement to report.

## Calibration record (online-gaming, 2026-10-18, read only)

Takes used, with the verdict the project already held: the screen 13/17 (pass) and 35 (wrong
speaker); the quiet kids 46 (one line missing), 51 (direction text spoken), 52 (lines land),
5 (Tommy alone, user pick); Get broker 12/13 (Dev alone) and 46 (jargon); double order 16
("sounds British"), 17/18 (pass, voice from Tommy wins); swatted hand 68 (voice wrong) and 69
(usable); not because 34; Tommy wins 10; the smart kids 6; kids like me 6; good presents 20;
things I cared about 24 (gibberish bits); room tone 2 (gibberish voice). What each verb got right
and wrong is in the sections above and in `skill-improvement-notes.md`.
