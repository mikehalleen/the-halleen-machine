# Params

`generate --bin <id> --params '{...}'` takes a JSON object. The server merges
it over the project and bin defaults, hands the result to the runner for the
bin's kind, and records the merged params on the generation (`recipe <media>`,
`history --bin <id>`).

## Merge order

1. Project canvas: `width` and `height` from `set_project_settings`, when the
   project is configured.
2. Bin: `length_s`, video bins only, when the bin has one.
3. Caller: whatever you pass in `--params`. Caller keys win.

An override is one-off: it does not change the project or the bin, and the
next generate without it goes back to the defaults.

## Image params

| key | type | effect |
|---|---|---|
| `seed` | int | sampler seed; omitted means a random seed is drawn, then recorded |
| `steps` | int | sampler steps |
| `cfg` | float | classifier-free guidance strength |
| `sampler` | str | sampler name as the workflow's KSampler knows it |
| `scheduler` | str | scheduler name |
| `width` | int | canvas width; from project defaults unless overridden |
| `height` | int | canvas height; from project defaults unless overridden |
| `draft` | bool | the workflow's own draft setting (a `THM-IsDraft` switch); only where `bin` says `draft=supported` |

## Video params

| key | type | effect |
|---|---|---|
| `seed` | int | sampler seed; omitted means a random seed is drawn, then recorded |
| `steps` | int | total sampler steps; split across passes on multi-pass workflows |
| `fps` | float | frame rate; omitted means the workflow's own rate |
| `length_s` | float | clip length in seconds; from the bin unless overridden; frames = round(length_s * fps) + 1 |
| `quarter` | bool | draft render: halves both sides of the canvas, a quarter of the pixels |
| `draft` | bool | the workflow's own draft setting (a `THM-IsDraft` switch: fewer steps, a distilled model...); only where `bin` says `draft=supported`, a no-op elsewhere. Images take it too |
| `width` | int | canvas width; from project defaults unless overridden |
| `height` | int | canvas height; from project defaults unless overridden |

A draft render on a workflow with a draft setting carries both: `{"quarter": true,
"draft": true}`. `quarter` needs a canvas to halve, so it is a no-op without `width` and
`height` (from the project or from you); the halving is a plain integer
divide. Some workflow families round the frame count down to their own grid,
so a requested length can land a few frames short.

## Unknown keys

The runner reads only the keys above and silently ignores the rest. The CLI
warns on an unrecognised key; treat the warning as "this did nothing".

## What is not a param

- **loras**: `bin.loras`, set with `set_bin_field loras` using names from `loras`. A
  `<lora:name:strength>` tag in a prompt is lifted into the same field. The server spells each
  name the way the file is (a dropped extension or folder is filled in when one file matches)
  and refuses `unknown_lora` when none does.
- **start and end frame**: reference positions 1 and 2 on a video bin
  (`bind_reference` with `position`), used when the workflow supports them.
- **negative**: `bin.negative`, merged with the project negative at render time.

## Seed

Absent, the runner draws one and records it on the generation, so every take
is reproducible from its recipe. Pass it back to reproduce a take or to change
one clause against a fixed roll. Spread exploratory seeds (`10000`, `20000`,
`30000`); consecutive seeds give near-duplicates.

## The ladder's typical sets

Draft video, proving a direction (rung 1; add `"draft": true` where supported):

```
{"quarter": true, "draft": true, "length_s": 3, "seed": 10000}
```

Draft video, holding the direction over the full length (rung 2):

```
{"quarter": true, "draft": true, "seed": 10000}
```

Full-size candidate on the seed that held (rung 3):

```
{"seed": 10000}
```

Still, exploring a new look at a small canvas (repeat with `20000`, `30000`):

```
{"width": 512, "height": 768, "seed": 10000}
```

Still, one clause changed on a known-good roll at project size:

```
{"seed": 20000}
```

No `--params` at all is the plain project-plus-bin recipe with a fresh seed:
the right call for a first render in a new bin.
