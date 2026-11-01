"""The stitcher: a sequence rendered to one mp4.

Every item is one ffmpeg input, normalised to the project canvas (fit inside, letterboxed, never
upscaled past the fit, no frame interpolation) and the sequence's frame rate, conformed to
exactly its frame count (trimmed if long, last frame cloned if short), then concatenated in one
``-filter_complex``. A video item is its selected take, trimmed; a still shows for its length; a
bin with no take yet shows a title card with the bin's name. Sound is each clip's own, or silence
where a clip has none, trimmed and padded to exactly ``frames / fps`` seconds so picture and
sound never drift apart over a long sequence.

Sound layers (``Sequence.layers``: a song, a bed) are each one input laid down continuously from
``source_in_s`` at ``start_frame``: never cut into pieces and joined, so there is no seam to pop
and no drift to accumulate. Each item's per-layer offset (``ItemMix``) becomes a piecewise gain
envelope with linear ramps at the item's edges; the clip audio and the layers are mixed without
normalisation and held under -1 dBTP by a limiter. An export with no layers takes the same path
minus the mix stage.

Same invocation style as ``clipper.py``: ffmpeg off PATH, an argv list, no shell. ``build_args``
is pure so the filter graph can be tested without ffmpeg; ``stitch`` runs it. The item preview
(``build_preview_plan``) is the same plan and graph for one item with handles, so what the agent
hears is what the export will have.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .models import STILL_DEFAULT_S, ItemMix, Project, Sequence, SoundLayer, stamp_take  # noqa: F401  (stamp_take re-exported for the export landing)

DEFAULT_FPS = 24.0
CARD_FILL = (0xE9, 0xE4, 0xD8)
CARD_INK = (0x1A, 0x1A, 0x18)
AUDIO_RATE = 48000
LIMIT_LINEAR = 0.891  # -1 dBTP
MUTE_GAIN = 0.0
ENVELOPE_SAMPLES = 240  # 5 ms at 48 kHz: the step of the gain envelope, well under any ramp


@dataclass
class PlanItem:
    bin: str
    kind: str  # video | still | title
    path: str  # the file ffmpeg reads: the take, the image, or the title card made for this export
    duration_s: float  # exactly frames / fps
    head_s: float = 0.0  # video: where to start in the source
    label: str = ""  # for the sidecar and warnings
    frames: int = 0
    start_frame: int = 0  # in the sequence timeline
    mix: ItemMix | None = None


@dataclass
class PlanLayer:
    id: str
    path: str
    source_in_s: float
    start_frame: int  # in the sequence timeline
    end: str  # picture | source
    gain_db: float = 0.0
    fade_in_s: float = 0.0
    fade_out_s: float = 0.0
    source_duration_s: float | None = None


@dataclass
class StitchPlan:
    items: list[PlanItem]
    width: int
    height: int
    fps: float
    skipped: list[str]  # bins skipped with the reason
    layers: list[PlanLayer] = field(default_factory=list)
    # the sequence frame the plan's first frame stands at: 0 for an export, the item's start
    # (less its head handle) for a preview, so the layers line up the same way in both
    offset_frames: int = 0

    @property
    def total_s(self) -> float:
        return sum(i.duration_s for i in self.items)

    @property
    def total_frames(self) -> int:
        return sum(frames_of(i, self.fps) for i in self.items)


def frames_of(item: PlanItem, fps: float) -> int:
    """The item's frame count: as planned, else its duration at the rate, rounded once."""
    return int(item.frames) if item.frames else max(1, int(round(item.duration_s * fps)))


def has_audio_stream(path) -> bool:
    """Whether the file carries an audio stream (ffprobe); False when it cannot be told."""
    exe = shutil.which("ffprobe")
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout or "{}")
    except Exception:
        return False
    return bool(isinstance(data, dict) and data.get("streams"))


def _font(size: int):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def title_card(text: str, width: int, height: int, dest: str | Path, *, number: int | None = None) -> Path:
    """A still with the bin's name (and shot number) on the design's card fill: what a bin with
    no take yet shows in the cut, so the sequence plays through at its full running time."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (max(16, width), max(16, height)), CARD_FILL)
    draw = ImageDraw.Draw(im)
    size = max(18, height // 10)
    font = _font(size)
    lines = [str(text).strip() or "untitled"]
    if number is not None:
        lines.insert(0, str(number))
    # shrink until the longest line fits inside 85% of the width
    while size > 12:
        widths = [draw.textlength(line, font=font) for line in lines]
        if max(widths) <= width * 0.85:
            break
        size -= 2
        font = _font(size)
    gap = size // 3
    total = len(lines) * size + (len(lines) - 1) * gap
    y = (height - total) / 2
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        fill = (0x6E, 0x6E, 0x68) if number is not None and i == 0 else CARD_INK
        draw.text(((width - w) / 2, y), line, font=font, fill=fill)
        y += size + gap
    im.save(dest, format="PNG")
    return dest


def _plan_layers(project: Project, seq: Sequence) -> list[PlanLayer]:
    out: list[PlanLayer] = []
    for lay in seq.layers:
        media = project.media_by_id.get(lay.media)
        if media is None or not Path(media.path).is_file():
            continue
        out.append(PlanLayer(
            id=lay.id, path=media.path, source_in_s=float(lay.source_in_s), start_frame=int(lay.start_frame), end=lay.end,
            gain_db=float(lay.gain_db), fade_in_s=float(lay.fade_in_s), fade_out_s=float(lay.fade_out_s),
            source_duration_s=float(media.duration_s) if media.duration_s else None,
        ))
    return out


def build_plan(project: Project, seq: Sequence, work_dir: str | Path, *, now_tag: str) -> StitchPlan:
    """What ffmpeg will read, in order. Title cards are rendered into ``work_dir`` for this export.
    Every item gets its exact frame count at the sequence rate; durations are those frames."""
    from .commands import item_frames, item_media, sequence_fps, trim_dropped  # local: commands imports models, not this

    width = int(project.defaults.width or 0) or 1280
    height = int(project.defaults.height or 0) or 720
    fps = sequence_fps(project, seq)
    items: list[PlanItem] = []
    skipped: list[str] = []
    start_frame = 0
    for i, item in enumerate(seq.items):
        bin_ = project.bins_by_id.get(item.bin)
        if bin_ is None:
            skipped.append(f"{item.bin}: bin is gone")
            continue
        media = item_media(project, item)
        frames = item_frames(project, item, fps)
        dur = frames / fps
        label = f'shot {bin_.shot} "{bin_.name}"' if bin_.shot else bin_.name
        if media is None:
            card = title_card(bin_.name, width, height, Path(work_dir) / f"card_{now_tag}_{i + 1:02d}.png", number=bin_.shot)
            items.append(PlanItem(bin=bin_.id, kind="title", path=str(card), duration_s=dur, label=label, frames=frames, start_frame=start_frame, mix=item.mix))
        elif not Path(media.path).is_file():
            skipped.append(f"{label}: file missing ({media.path})")
            continue
        elif media.type == "video":
            head = 0.0 if trim_dropped(project, item) else float(item.head_s or 0.0)
            if frames <= 0:
                skipped.append(f"{label}: nothing left after the trim")
                continue
            items.append(PlanItem(bin=bin_.id, kind="video", path=media.path, duration_s=dur, head_s=head, label=label, frames=frames, start_frame=start_frame, mix=item.mix))
        else:
            items.append(PlanItem(bin=bin_.id, kind="still", path=media.path, duration_s=dur, label=label, frames=frames, start_frame=start_frame, mix=item.mix))
        start_frame += frames
    return StitchPlan(items=items, width=width, height=height, fps=fps, skipped=skipped, layers=_plan_layers(project, seq))


def build_preview_plan(project: Project, seq: Sequence, bin_id: str, work_dir: str | Path, *, now_tag: str, handles_s: float = 0.0) -> StitchPlan:
    """One item of the sequence with its layer slices and mix, through the same plan the export
    uses, plus up to ``handles_s`` of the take before and after it (as far as the take allows;
    a still or a card gets no handles). The layers are placed as they are in the sequence."""
    full = build_plan(project, seq, work_dir, now_tag=now_tag)
    from .commands import CommandRefused, item_media

    target = next((it for it in full.items if it.bin == bin_id), None)
    if target is None:
        raise CommandRefused("not_in_sequence", f"{bin_id} is not in {seq.name} (or was skipped from the plan)", {"type": "bin", "id": bin_id, "skipped": full.skipped})
    item = next(it for it in seq.items if it.bin == bin_id)
    media = item_media(project, item)
    head_handle = tail_handle = 0
    if target.kind == "video" and media is not None and handles_s > 0:
        want = int(round(handles_s * full.fps))
        head_handle = min(want, int(math.floor(target.head_s * full.fps)))
        source_frames = int(math.floor(float(media.duration_s or 0.0) * full.fps))
        tail_handle = max(0, min(want, source_frames - int(round(target.head_s * full.fps)) - target.frames))
    frames = target.frames + head_handle + tail_handle
    one = PlanItem(
        bin=target.bin, kind=target.kind, path=target.path, duration_s=frames / full.fps,
        head_s=max(0.0, target.head_s - head_handle / full.fps), label=target.label, frames=frames,
        start_frame=0, mix=target.mix,
    )
    return StitchPlan(items=[one], width=full.width, height=full.height, fps=full.fps, skipped=full.skipped, layers=full.layers, offset_frames=target.start_frame - head_handle)


def _scale_chain(w: int, h: int, fps: float) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:g},format=yuv420p"


def _conform(frames: int) -> str:
    """Exactly ``frames`` after the fps filter: clone the last frame if short, trim if long."""
    return f"tpad=stop_mode=clone:stop={frames},trim=end_frame={frames},setpts=PTS-STARTPTS"


def _db(value: float) -> float:
    return 10.0 ** (float(value) / 20.0)


def _num(x: float) -> str:
    return f"{x:.6g}"


def _item_audio_tail(item: PlanItem) -> str:
    """The item's own sound, shaped by its mix: level, and a fade at both edges."""
    parts: list[str] = []
    mix = item.mix
    if mix is None:
        return ""
    fade = max(0, int(mix.edge_fade_ms)) / 1000.0
    if fade > 0 and item.duration_s > 2 * fade:
        parts.append(f"afade=t=in:st=0:d={_num(fade)}")
        parts.append(f"afade=t=out:st={_num(item.duration_s - fade)}:d={_num(fade)}")
    if mix.clip_db:
        parts.append(f"volume={_num(mix.clip_db)}dB")
    return ("," + ",".join(parts)) if parts else ""


def layer_envelope(plan: StitchPlan, layer: PlanLayer) -> str | None:
    """The per-item gain on a layer as one ``volume`` expression in the layer's own time (t = 0 at
    its first sample): a constant per item, a linear ramp of the item's ``ramp_ms`` at each item
    edge, nothing at all when no item changes it. Muted items ramp to silence the same way."""
    segments: list[tuple[float, float, float]] = []  # (start_s in layer time, gain, ramp_s)
    # the layer's first sample in this plan: its own start, or the plan's start when the plan
    # (a preview) begins after the layer did and the chain trims the in point up accordingly
    base = max(layer.start_frame, plan.offset_frames)
    for item in plan.items:
        mix = item.mix
        offset = (item.start_frame + plan.offset_frames - base) / plan.fps
        if mix is not None and layer.id in mix.layers:
            value = mix.layers[layer.id]
            gain = MUTE_GAIN if value is None else _db(value)
        else:
            gain = 1.0
        ramp = (max(0, int(mix.ramp_ms)) / 1000.0) if mix is not None else 0.03
        if offset + item.duration_s <= 0:
            continue  # the item ends before the layer starts: nothing of it is heard on this layer
        segments.append((max(0.0, offset), gain, ramp))
    if all(abs(g - 1.0) < 1e-9 for _, g, _ in segments):
        return None
    # nested from the last item backwards: if(t < start_i, <earlier>, if(t < start_i + ramp, <ramp>, gain_i))
    pieces: list[tuple[float, float, float, float]] = []
    for i, (start, gain, ramp) in enumerate(segments):
        prev = segments[i - 1][1] if i > 0 else 1.0
        pieces.append((start, prev, gain, ramp))
    expr = None
    for start, prev, gain, ramp in reversed(pieces):
        rest = expr if expr is not None else _num(gain)
        if ramp > 0 and abs(gain - prev) > 1e-9 and start > 0:
            ramped = f"{_num(prev)}+({_num(gain)}-{_num(prev)})*(t-{_num(start)})/{_num(ramp)}"
            expr = f"if(lt(t,{_num(start)}),{{PREV}},if(lt(t,{_num(start + ramp)}),{ramped},{rest}))"
        elif start > 0:
            expr = f"if(lt(t,{_num(start)}),{{PREV}},{rest})"
        else:
            expr = rest
        expr = expr.replace("{PREV}", _num(prev), 1) if "{PREV}" in expr else expr
    return expr


def _layer_chain(plan: StitchPlan, layer: PlanLayer, idx: int, label: str) -> str:
    """One layer as one continuous input: trimmed at its in point, faded, gained, enveloped per
    item, delayed to its start frame. Never split."""
    total_s = plan.total_frames / plan.fps
    start_rel = (layer.start_frame - plan.offset_frames) / plan.fps  # may be negative: the layer began before this plan's first frame
    source_in = layer.source_in_s + max(0.0, -start_rel)
    delay_s = max(0.0, start_rel)
    length = total_s - delay_s  # end: picture
    if layer.end == "source" and layer.source_duration_s is not None:
        length = min(length, max(0.0, layer.source_duration_s - source_in))
    parts = [
        f"[{idx}:a]aresample={AUDIO_RATE}",
        f"aformat=sample_fmts=fltp:channel_layouts=stereo",
        f"atrim=start={_num(source_in)}:end={_num(source_in + max(length, 0.0))}",
        "asetpts=PTS-STARTPTS",
    ]
    if layer.fade_in_s > 0:
        parts.append(f"afade=t=in:st=0:d={_num(layer.fade_in_s)}")
    if layer.fade_out_s > 0 and length > layer.fade_out_s:
        parts.append(f"afade=t=out:st={_num(length - layer.fade_out_s)}:d={_num(layer.fade_out_s)}")
    if layer.gain_db:
        parts.append(f"volume={_num(layer.gain_db)}dB")
    env = layer_envelope(plan, layer)
    if env is not None:
        parts.append(f"asetnsamples=n={ENVELOPE_SAMPLES}")
        parts.append(f"volume=volume='{env}':eval=frame")
    if delay_s > 0:
        ms = int(round(delay_s * 1000))
        parts.append(f"adelay={ms}|{ms}")
    return ",".join(parts) + f"[{label}]"


def build_args(plan: StitchPlan, dest: str | Path, *, exe: str = "ffmpeg", audio_of: Callable[[str], bool] | None = None) -> list[str]:
    """The whole ffmpeg command. ``audio_of`` says whether a video file has sound (default:
    ffprobe); a silent one gets ``anullsrc`` for its length so the concat stays aligned."""
    audio_of = audio_of or has_audio_stream
    args: list[str] = [exe, "-y", "-v", "error", "-nostdin"]
    chains: list[str] = []
    v_labels: list[str] = []
    a_labels: list[str] = []
    n_inputs = 0
    for i, item in enumerate(plan.items):
        if item.kind == "video":
            args += ["-ss", f"{item.head_s:.3f}", "-t", f"{item.duration_s + 1.0 / plan.fps:.3f}", "-i", item.path]
        else:
            args += ["-loop", "1", "-t", f"{item.duration_s:.3f}", "-r", f"{plan.fps:g}", "-i", item.path]
        idx = n_inputs
        n_inputs += 1
        chains.append(f"[{idx}:v]{_scale_chain(plan.width, plan.height, plan.fps)},{_conform(frames_of(item, plan.fps))}[v{i}]")
        v_labels.append(f"[v{i}]")
        tail = _item_audio_tail(item)
        if item.kind == "video" and audio_of(item.path):
            chains.append(f"[{idx}:a]aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=0:{item.duration_s:.3f},asetpts=PTS-STARTPTS,apad=whole_dur={item.duration_s:.3f}{tail}[a{i}]")
        else:
            chains.append(f"anullsrc=r={AUDIO_RATE}:cl=stereo,atrim=0:{item.duration_s:.3f},asetpts=PTS-STARTPTS[a{i}]")
        a_labels.append(f"[a{i}]")
    n = len(plan.items)
    pairs = "".join(f"{v}{a}" for v, a in zip(v_labels, a_labels))
    if plan.layers:
        chains.append(f"{pairs}concat=n={n}:v=1:a=1[vout][aclips]")
        labels = []
        for k, layer in enumerate(plan.layers):
            args += ["-i", layer.path]
            label = f"l{k}"
            chains.append(_layer_chain(plan, layer, n_inputs, label))
            labels.append(f"[{label}]")
            n_inputs += 1
        chains.append(f"[aclips]{''.join(labels)}amix=inputs={len(labels) + 1}:duration=first:dropout_transition=0:normalize=0,alimiter=limit={LIMIT_LINEAR}:level=false:attack=5:release=50[aout]")
    else:
        chains.append(f"{pairs}concat=n={n}:v=1:a=1[vout][aout]")
    args += [
        "-filter_complex", ";".join(chains),
        "-map", "[vout]", "-map", "[aout]",
        # no tags from the inputs: the first clip's ComfyUI graph would otherwise ride along and
        # make the export read as a render of that one bin (recipe, recall)
        "-map_metadata", "-1",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-progress", "pipe:1", str(dest),
    ]
    return args


def parse_progress_line(line: str) -> float | None:
    """``out_time_ms=1234567`` (microseconds, despite the name) -> seconds; None for other lines."""
    line = line.strip()
    if line.startswith("out_time_ms="):
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    if line.startswith("out_time_us="):
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    return None


def stitch(plan: StitchPlan, dest: str | Path, *, on_progress: Callable[[float], None] | None = None, timeout: float = 1800) -> Path:
    """Run the export to ``dest`` (synchronous; the server runs the same args under asyncio).
    ``on_progress`` gets a 0..1 fraction as ffmpeg reports time."""
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    if not plan.items:
        raise ValueError("nothing to export")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = build_args(plan, dest, exe=exe)
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    total = plan.total_s or 1.0
    assert proc.stdout is not None
    for line in proc.stdout:
        t = parse_progress_line(line)
        if t is not None and on_progress is not None:
            on_progress(max(0.0, min(1.0, t / total)))
    _, err = proc.communicate(timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((err or "").strip()[-2000:] or f"ffmpeg exited {proc.returncode}")
    if not dest.is_file() or dest.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced no output")
    if on_progress is not None:
        on_progress(1.0)
    return dest
