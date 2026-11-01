# Building from a song

The recipe for a piece cut to music: the shots land on the song's phrases, the action
follows its intensity, the song sits under the export in one continuous piece, and the
finished export is checked against the beat. Every number comes from the `audio` tool
(`tools`; read `thm-tools/audio/GUIDE.md` once); every change to the project is a command.

## 1. The song arrives

Either way it is ordinary media in a bin:

- `create --name "Neon run" --brief "..." --audio neon.mp3` makes the project and puts the
  song into a `Music` bin tagged `music`, before any session (the one agent upload that
  needs none).
- Or the user drops the file into a bin in the app; `bin <id>` shows it with its length,
  sample rate and channels.

Audio is never a sequence item (`sequence add` refuses it: `audio_item`). It goes under the
sequence as a layer in step 4.

## 2. Read the song

```
tool audio beats <song> --fps <project fps>      # tempo, beats, downbeats, bars, phrases in frames
tool audio energy <song> --fps <project fps> --beats beats.json   # per-bar intensity, sections with a level
```

Save the beats JSON (`--scratch` names the folder) and pass it to `energy` so the song is
tracked once. Read `phrases["4"]` (and `["2"]`, `["8"]`) for the phrase starts and lengths in
whole frames: those frame counts come from cumulative positions, so a sequence built on them
stays locked to the song for its whole length. Read `sections` for where the song is `still`,
`calm`, `building`, `high` or `peak`, in bars and frames.

## 3. Plan the shots on the phrases

One shot per phrase, or per bar in a `peak` section and per two or four phrases in a `still`
one; the action level in each shot's description follows its section's level (a locked-off
frame under `calm`, movement and a cut on every downbeat under `peak`). Send the plan with
`propose-shots`: each item's `length_s` is the phrase's length plus a small handle at each end
(half a second each is plenty), so the take can be trimmed to the exact frames in step 4.
`filmmaking.md` still rules the coverage; the song only decides where the cuts fall.

## 4. Build the sequence and lay the song under it

```
sequence new --name "Neon run"
sequence add <seq> <bins in song order>
sequence trim <seq> <bin> --head <handle> --tail <handle>   # per item, to exactly its phrase's frames
sequence show <seq>                                        # check f<start>-<end> per item against the phrase starts
sequence layer-add <seq> music --media <song> --in 0 --summary "the song under the whole sequence"
```

Trim so that each item's `frames` in `sequence show` equals its phrase's `frames` and its
`start_frame` equals the phrase's `start_frame`; the project's video fps is the sequence's fps,
so the numbers match one for one. `--in` is where in the song the sequence starts (0, or the
first chorus); `--start-frame` lets the song begin under a later item; `--end source` stops it
when the song runs out instead of at the end of picture.

## 5. QC each item in place

For each item: `sequence preview <seq> <bin> --out shot.mp4`, then QC picture (`frames`) and
sound (`qc.md`: `levels` on the preview; `transcribe`/`voice` where the shot has a line) and
mark the take. Then set the mix for that item and record the listen:

```
sequence mix <seq> <bin> --layer music=-8 --summary "music down under the line"
sequence mix <seq> <bin> --layer music=mute --summary "no music under the reaction"
sequence mix <seq> <bin> --reviewed <its start_frame>
```

Any later trim or insert before an item moves it under the song: `sequence show` marks it
`MOVED since your review` and `resume` lists it. Preview those again.

## 6. Export and check the cut against the beat

```
sequence export <seq> --wait
tool audio cuts <export media> --song <song> --song-in <the layer's --in> --fps <fps> [--start-frame <the layer's>]
tool audio motion <export media>
```

`cuts` lists every scene cut with its distance to the nearest beat and downbeat in frames;
report every cut more than one frame off the beat with the item it belongs to. `motion` per
second against the `energy` sections is the second report: a shot that measures like a still
under a `peak` section, or a whip under a `still` one, disagrees with the song. A level change,
a mute or a different `--in` is a mix command and another export, never a re-render.
