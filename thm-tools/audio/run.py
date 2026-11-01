"""THM audio tool: measure sound, print JSON, change nothing.

    python -I run.py <verb> <media or path> [args] --scratch DIR

Run by ``thm-agent/cli.py tool audio <verb> ...``, which fetches media ids to files first and sets
CUDA_VISIBLE_DEVICES="" and HF_HOME/TORCH_HOME to ``models/`` beside this file. Every verb reads
audio from any media (a video's audio track too) through ffmpeg. Output is one JSON object on
stdout, ASCII only; files go under ``--scratch``. Exit 0 on a result, 1 on a failure (the JSON
carries ``error``), 2 on a bad invocation. GUIDE.md says what each number means and where the
thresholds come from.

Heavy imports (torch, whisper, speechbrain, beat_this, librosa, parselmouth) are loaded by the
verb that needs them, so ``levels``, ``motion`` and ``cuts`` run with ffmpeg and numpy alone.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MODELS_DIR = HERE / "models"
DEFAULT_WHISPER = "small"  # calibration 2026-10-18: small 6-9 s per take on CPU, medium 16-24 s (GUIDE.md)
WHISPER_COMPUTE = "int8"
ECAPA_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
BEAT_THIS_CHECKPOINT = "final0"
LOW_CONFIDENCE = 0.5  # a whisper word below this probability is flagged: garble can read as a real word
VOICE_BAND = (300.0, 3400.0)
LEVEL_WINDOW_S = 0.25
EDGE_S = 0.3
CLICK_JUMP = 0.2  # a sample-to-sample jump this large (full scale) with quiet neighbours is a click
EDGE_OVER_DB = 3.0  # an edge window this far above the body's P90 is a stray burst, not the line starting
HOT_PEAK_DBFS = 4.0  # generated takes sit at 2-3 dBFS after decoding; above this is an outlier
SCENE_THRESHOLD = 0.12  # mean grey change 0..1 between neighbouring frames that counts as a cut


class ToolError(Exception):
    pass


# -- helpers -----------------------------------------------------------------------


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ToolError("ffmpeg is not on PATH")
    return exe


def _ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise ToolError("ffprobe is not on PATH")
    return exe


def probe(path: str) -> dict:
    """Streams and duration of a file: ``{"duration_s", "audio": bool, "video": bool, "fps"}``."""
    r = subprocess.run([_ffprobe(), "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate,duration", "-show_entries", "format=duration", "-of", "json", path], capture_output=True, text=True, timeout=60)
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        data = {}
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    out = {"duration_s": None, "audio": False, "video": False, "fps": None}
    try:
        out["duration_s"] = float(fmt.get("duration")) if fmt.get("duration") else None
    except (TypeError, ValueError):
        pass
    for s in streams:
        if s.get("codec_type") == "audio":
            out["audio"] = True
        if s.get("codec_type") == "video":
            out["video"] = True
            rate = str(s.get("r_frame_rate") or "")
            if "/" in rate:
                a, b = rate.split("/", 1)
                out["fps"] = float(a) / float(b) if float(b) else None
            elif rate:
                out["fps"] = float(rate)
    return out


def decode(path: str, sr: int = 16000, *, start: float | None = None, end: float | None = None) -> np.ndarray:
    """Mono float32 samples at ``sr`` from any media, via ffmpeg. A window trims the source."""
    if not Path(path).is_file():
        raise ToolError(f"no such file: {path}")
    info = probe(path)
    if not info["audio"]:
        raise ToolError(f"{Path(path).name} has no audio stream")
    cmd = [_ffmpeg(), "-v", "error", "-nostdin"]
    if start is not None and start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", path, "-vn", "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(sr), "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise ToolError(f"ffmpeg could not decode {Path(path).name}: {r.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    samples = np.frombuffer(r.stdout, dtype=np.float32)
    if samples.size == 0:
        raise ToolError(f"{Path(path).name} decoded to no samples")
    return samples.copy()


def db(x: float, floor: float = -120.0) -> float:
    return float(20.0 * math.log10(x)) if x > 0 else floor


def frames_at(t: float | None, fps: float | None) -> int | None:
    return int(round(t * fps)) if t is not None and fps else None


def with_frames(d: dict, fps: float | None, keys: tuple[str, ...]) -> dict:
    """Add ``<key>_frame`` beside each time key when an fps is known."""
    if fps:
        for k in keys:
            if d.get(k) is not None:
                d[k.replace("_s", "") + "_frame"] = frames_at(float(d[k]), fps)
    return d


def normalize_words(text: str) -> list[str]:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return [w.strip("'") for w in text.split() if w.strip("'")]


def _window(spec: str | None) -> tuple[float | None, float | None]:
    if not spec:
        return None, None
    m = re.match(r"^\s*([0-9.]*)\s*:\s*([0-9.]*)\s*$", spec)
    if not m:
        raise ToolError(f"--window wants s0:s1 (seconds), got {spec!r}")
    s0 = float(m.group(1)) if m.group(1) else None
    s1 = float(m.group(2)) if m.group(2) else None
    return s0, s1


# -- transcribe ----------------------------------------------------------------------


_whisper_cache: dict = {}


def whisper_model(size: str):
    from faster_whisper import WhisperModel

    if size not in _whisper_cache:
        root = MODELS_DIR / "faster-whisper"
        root.mkdir(parents=True, exist_ok=True)
        _whisper_cache[size] = WhisperModel(size, device="cpu", compute_type=WHISPER_COMPUTE, download_root=str(root))
    return _whisper_cache[size]


def transcribe_words(path: str, *, model_size: str, language: str | None, start: float | None = None, end: float | None = None) -> dict:
    audio = decode(path, 16000, start=start, end=end)
    t0 = time.monotonic()
    model = whisper_model(model_size)
    segments, info = model.transcribe(audio, word_timestamps=True, beam_size=5, language=language, condition_on_previous_text=False)
    words = []
    text_parts = []
    offset = float(start or 0.0)
    for seg in segments:
        text_parts.append(seg.text.strip())
        for w in seg.words or []:
            words.append({"word": w.word.strip(), "start_s": round(w.start + offset, 3), "end_s": round(w.end + offset, 3), "prob": round(float(w.probability), 3)})
    return {
        "text": " ".join(p for p in text_parts if p),
        "language": info.language,
        "language_prob": round(float(info.language_probability), 3),
        "words": words,
        "audio_s": round(len(audio) / 16000.0, 3),
        "model": model_size,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


def compare_expected(expected: str, words: list[dict]) -> dict:
    """Word error rate and a word-level diff of what was heard against the line as written."""
    import jiwer

    ref = normalize_words(expected)
    hyp = [w for word in words for w in normalize_words(word["word"])]
    if not ref:
        raise ToolError("--expect is empty after normalization")
    if not hyp:
        return {"expected_words": ref, "heard_words": [], "wer": 1.0, "diff": [{"op": "missing", "expected": w, "heard": None} for w in ref], "match": False}
    out = jiwer.process_words(" ".join(ref), " ".join(hyp))
    diff = []
    for chunk in out.alignments[0]:
        if chunk.type == "equal":
            continue
        r_words = ref[chunk.ref_start_idx : chunk.ref_end_idx]
        h_words = hyp[chunk.hyp_start_idx : chunk.hyp_end_idx]
        if chunk.type == "substitute":
            for a, b in zip(r_words, h_words):
                diff.append({"op": "substituted", "expected": a, "heard": b})
        elif chunk.type == "delete":
            for a in r_words:
                diff.append({"op": "missing", "expected": a, "heard": None})
        elif chunk.type == "insert":
            for b in h_words:
                diff.append({"op": "extra", "expected": None, "heard": b})
    return {"expected_words": ref, "heard_words": hyp, "wer": round(float(out.wer), 3), "diff": diff, "match": out.wer == 0}


def verb_transcribe(args, fps):
    result = transcribe_words(args.media, model_size=args.model, language=args.language)
    for w in result["words"]:
        with_frames(w, fps, ("start_s", "end_s"))
    low = [w["word"] for w in result["words"] if w["prob"] < LOW_CONFIDENCE]
    result["low_confidence"] = low
    if args.expect:
        result["expected"] = args.expect
        result.update(compare_expected(args.expect, result["words"]))
        result["verdict"] = "line correct" if result["match"] else f"{len(result['diff'])} word difference(s)"
        if low:
            result["verdict"] += f"; {len(low)} low-confidence word(s), listen before trusting them"
    return result


# -- voice -------------------------------------------------------------------------


_ecapa = None


def ecapa():
    global _ecapa
    if _ecapa is None:
        import torch
        from speechbrain.inference.speaker import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy

        torch.set_num_threads(max(1, os.cpu_count() or 1))
        savedir = MODELS_DIR / "spkrec-ecapa-voxceleb"
        savedir.mkdir(parents=True, exist_ok=True)
        # copies, not symlinks: a symlink needs a privilege a plain Windows account does not have
        _ecapa = EncoderClassifier.from_hparams(source=ECAPA_SOURCE, savedir=str(savedir), run_opts={"device": "cpu"}, local_strategy=LocalStrategy.COPY)
    return _ecapa


def embed_voice(path: str, start: float | None = None, end: float | None = None):
    import torch

    audio = decode(path, 16000, start=start, end=end)
    if audio.size < 16000 // 4:
        raise ToolError(f"{Path(path).name}: less than a quarter second of audio to embed")
    with torch.no_grad():
        emb = ecapa().encode_batch(torch.from_numpy(audio)[None, :])
    vec = emb.squeeze().cpu().numpy().astype(np.float64)
    return vec / (np.linalg.norm(vec) or 1.0)


def verb_voice(args, fps):
    s0, s1 = _window(args.window)
    refs = [r for part in (args.ref or []) for r in part.split(",") if r]
    if not refs:
        raise ToolError("--ref needs at least one reference media")
    t0 = time.monotonic()
    line = embed_voice(args.media, s0, s1)
    sims = []
    for ref in refs:
        score = float(np.dot(line, embed_voice(ref)))
        sims.append({"ref": ref, "name": Path(ref).stem, "score": round(score, 3)})
    ranked = sorted(sims, key=lambda s: s["score"], reverse=True)
    best = ranked[0]
    margin = round(best["score"] - ranked[1]["score"], 3) if len(ranked) > 1 else None
    return {
        "media": args.media,
        "window": [s0, s1] if (s0 is not None or s1 is not None) else None,
        "similarities": sims,
        "best": {"ref": best["ref"], "name": best["name"], "score": best["score"]},
        "margin": margin,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


# -- emphasis ----------------------------------------------------------------------


def intended_stress(expected: str) -> list[str]:
    """The words written in caps: two letters or more, all upper, so ``I`` and ``TV`` are not stress."""
    out = []
    for raw in expected.split():
        letters = re.sub(r"[^A-Za-z']", "", raw)
        if len(letters) >= 2 and letters.isupper() and letters not in ("TV", "OK", "US", "UK", "AI"):
            out.append(normalize_words(raw)[0] if normalize_words(raw) else letters.lower())
    return out


def align_expected(expected_words: list[str], heard: list[dict]) -> list[dict]:
    """Each heard word tagged with the expected word it stands for (by sequence alignment), so
    the intended stress can be looked up even when whisper misheard a neighbour."""
    import difflib

    heard_norm = [" ".join(normalize_words(w["word"])) for w in heard]
    sm = difflib.SequenceMatcher(a=expected_words, b=heard_norm, autojunk=False)
    tagged = [dict(w, expected=None) for w in heard]
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("equal", "replace"):
            for k in range(min(i2 - i1, j2 - j1)):
                tagged[j1 + k]["expected"] = expected_words[i1 + k]
    return tagged


def prosody(path: str, words: list[dict]) -> list[dict]:
    import parselmouth

    audio = decode(path, 16000)
    snd = parselmouth.Sound(audio.astype(np.float64), sampling_frequency=16000)
    pitch = snd.to_pitch(time_step=0.01, pitch_floor=70.0, pitch_ceiling=450.0)
    intensity = snd.to_intensity(time_step=0.01, minimum_pitch=70.0)
    p_times = pitch.xs()
    p_vals = pitch.selected_array["frequency"]
    i_times = intensity.xs()
    i_vals = intensity.values[0]
    out = []
    for w in words:
        s, e = float(w["start_s"]), float(w["end_s"])
        pm = (p_times >= s) & (p_times <= e) & (p_vals > 0)
        im = (i_times >= s) & (i_times <= e)
        f0_peak = float(np.max(p_vals[pm])) if pm.any() else None
        f0_mean = float(np.mean(p_vals[pm])) if pm.any() else None
        i_peak = float(np.max(i_vals[im])) if im.any() else None
        i_mean = float(np.mean(i_vals[im])) if im.any() else None
        out.append(dict(w, f0_peak_hz=round(f0_peak, 1) if f0_peak else None, f0_mean_hz=round(f0_mean, 1) if f0_mean else None, intensity_peak_db=round(i_peak, 1) if i_peak is not None else None, intensity_mean_db=round(i_mean, 1) if i_mean is not None else None, duration_s=round(e - s, 3)))
    return out


def _z(values: list[float | None]) -> list[float]:
    arr = np.array([v if v is not None else np.nan for v in values], dtype=np.float64)
    good = arr[~np.isnan(arr)]
    if good.size < 2 or float(np.std(good)) == 0.0:
        return [0.0 for _ in values]
    mu, sd = float(np.mean(good)), float(np.std(good))
    return [0.0 if np.isnan(v) else (float(v) - mu) / sd for v in arr]


def verb_emphasis(args, fps):
    if not args.expect:
        raise ToolError("emphasis needs --expect with the stressed word(s) in CAPS")
    tr = transcribe_words(args.media, model_size=args.model, language=args.language)
    exp_words = normalize_words(args.expect)
    intended = intended_stress(args.expect)
    tagged = align_expected(exp_words, tr["words"])
    measured = prosody(args.media, tagged)
    # prominence: pitch peak and loudness peak carry most of English stress; length a little
    z_f0 = _z([w["f0_peak_hz"] for w in measured])
    z_int = _z([w["intensity_peak_db"] for w in measured])
    z_dur = _z([w["duration_s"] for w in measured])
    for w, a, b, c in zip(measured, z_f0, z_int, z_dur):
        w["prominence"] = round(0.4 * a + 0.4 * b + 0.2 * c, 3)
        w["intended"] = bool(w.get("expected") in intended)
        with_frames(w, fps, ("start_s", "end_s"))
    # function words rarely carry the intended stress; the comparison is among content words and the intended ones
    ranked = sorted(measured, key=lambda w: w["prominence"], reverse=True)
    top = ranked[0] if ranked else None
    intended_scores = [w["prominence"] for w in measured if w["intended"]]
    matches = None
    margin = None
    if top is not None and intended:
        matches = bool(top["intended"])
        if matches:
            others = [w["prominence"] for w in ranked if not w["intended"]]
            margin = round(top["prominence"] - (others[0] if others else 0.0), 3)
        else:
            margin = round(top["prominence"] - (max(intended_scores) if intended_scores else 0.0), 3)
    landed_on = top["word"] if top else None
    verdict = "no stress marked in --expect (no CAPS word)" if not intended else (
        "stress landed on the intended word" if matches else f"stress landed on '{landed_on}', not on {', '.join(repr(w) for w in intended)}")
    if intended and not any(w["intended"] for w in measured):
        verdict += "; the intended word was not heard at all (see transcribe --expect)"
    return {
        "media": args.media,
        "expected": args.expect,
        "intended": intended,
        "heard": tr["text"],
        "words": measured,
        "most_prominent": landed_on,
        "stress_matches": matches,
        "margin": margin,
        "verdict": verdict,
        "model": tr["model"],
        "elapsed_s": tr["elapsed_s"],
    }


# -- levels ------------------------------------------------------------------------


def _bandpass(x: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    """A voice band by FFT masking: no scipy needed, exact enough for loudness per window."""
    n = x.size
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    spec[(freqs < lo) | (freqs > hi)] = 0
    return np.fft.irfft(spec, n).astype(np.float32)


def verb_levels(args, fps):
    sr = 16000
    x = decode(args.media, sr)
    voice = _bandpass(x, sr, *VOICE_BAND)
    win = int(LEVEL_WINDOW_S * sr)
    windows = []
    for i in range(0, max(1, x.size - win + 1), win):
        seg, vseg = x[i : i + win], voice[i : i + win]
        full = db(float(np.sqrt(np.mean(seg * seg))))
        vb = db(float(np.sqrt(np.mean(vseg * vseg))))
        windows.append(with_frames({"start_s": round(i / sr, 3), "full_db": round(full, 1), "voice_db": round(vb, 1)}, fps, ("start_s",)))
    voice_levels = np.array([w["voice_db"] for w in windows])
    full_levels = np.array([w["full_db"] for w in windows])
    active = voice_levels[voice_levels > -60]
    p90 = float(np.percentile(active, 90)) if active.size else -120.0
    median = float(np.median(active)) if active.size else -120.0
    peak = db(float(np.max(np.abs(x))))
    # clicks and pops: a jump between neighbouring samples far above the local motion
    d = np.abs(np.diff(x))
    local = np.convolve(d, np.ones(64) / 64.0, mode="same") + 1e-4
    hits = np.where((d > CLICK_JUMP) & (d > 12 * local))[0]
    clicks = []
    last = -sr
    for idx in hits:
        if idx - last < sr * 0.05:
            continue
        last = idx
        t = idx / sr
        before = db(float(np.sqrt(np.mean(np.square(x[max(0, idx - 800) : idx]))))) if idx > 0 else -120.0
        after = db(float(np.sqrt(np.mean(np.square(x[idx + 1 : idx + 801])))))
        clicks.append(with_frames({"at_s": round(t, 3), "jump": round(float(d[idx]), 3), "before_db": round(before, 1), "after_db": round(after, 1)}, fps, ("at_s",)))
        if len(clicks) >= 50:
            break
    # stray bursts at the edges: the first and last 300 ms against the loudest speech windows
    # (P90 of the body), since a line that starts on frame one is normal and a burst louder than
    # any speech is not
    edge = int(EDGE_S * sr)
    head = db(float(np.sqrt(np.mean(np.square(x[:edge]))))) if x.size > edge else -120.0
    tail = db(float(np.sqrt(np.mean(np.square(x[-edge:]))))) if x.size > edge else -120.0
    body = full_levels[1:-1] if full_levels.size > 2 else full_levels
    loud_body = body[body > -60]
    body_p90 = float(np.percentile(loud_body, 90)) if loud_body.size else -120.0
    flags = []
    if clicks:
        flags.append(f"{len(clicks)} click(s)/pop(s)")
    if head > body_p90 + EDGE_OVER_DB:
        flags.append(f"loud head: {head:.1f} dB in the first {EDGE_S:g}s, above the body's loudest windows ({body_p90:.1f} dB P90)")
    if tail > body_p90 + EDGE_OVER_DB:
        flags.append(f"loud tail: {tail:.1f} dB in the last {EDGE_S:g}s, above the body's loudest windows ({body_p90:.1f} dB P90)")
    if peak > HOT_PEAK_DBFS:
        # generated dialogue decodes above full scale as a rule (2-3 dBFS on most takes); the
        # export's limiter takes care of that, so only a hot outlier is worth a word
        flags.append(f"hot: peak {peak:.1f} dBFS, well above full scale; the export limiter will squash it")
    if active.size == 0:
        flags.append("no voice-band energy above -60 dB: silent or not speech")
    return {
        "media": args.media,
        "duration_s": round(x.size / sr, 3),
        "window_s": LEVEL_WINDOW_S,
        "peak_dbfs": round(peak, 1),
        "voice_p90_db": round(p90, 1),
        "voice_median_db": round(median, 1),
        "windows": windows,
        "clicks": clicks,
        "edges": {"head_db": round(head, 1), "tail_db": round(tail, 1), "body_p90_db": round(body_p90, 1)},
        "flags": flags,
    }


# -- beats and energy ----------------------------------------------------------------


_beat_model = None


def beat_tracker():
    global _beat_model
    if _beat_model is None:
        import torch
        from beat_this.inference import Audio2Beats

        torch.set_num_threads(max(1, os.cpu_count() or 1))
        _beat_model = Audio2Beats(checkpoint_path=BEAT_THIS_CHECKPOINT, device="cpu", dbn=False)
    return _beat_model


def track_beats(path: str) -> tuple[np.ndarray, np.ndarray, float]:
    audio = decode(path, 22050)
    beats, downbeats = beat_tracker()(audio, 22050)
    return np.asarray(beats, dtype=np.float64), np.asarray(downbeats, dtype=np.float64), audio.size / 22050.0


def beat_structure(beats: np.ndarray, downbeats: np.ndarray, duration: float, fps: float | None) -> dict:
    if beats.size < 2:
        raise ToolError("fewer than two beats found; is this music?")
    intervals = np.diff(beats)
    tempo = 60.0 / float(np.median(intervals))
    if downbeats.size == 0:
        downbeats = beats[::4]
    per_bar = []
    for a, b in zip(downbeats[:-1], downbeats[1:]):
        per_bar.append(int(np.sum((beats >= a - 1e-3) & (beats < b - 1e-3))))
    beats_per_bar = int(np.bincount(per_bar).argmax()) if per_bar else 4
    bar_s = float(np.median(np.diff(downbeats))) if downbeats.size > 1 else beats_per_bar * 60.0 / tempo
    bars = []
    for i, start in enumerate(downbeats):
        end = float(downbeats[i + 1]) if i + 1 < downbeats.size else min(duration, float(start) + bar_s)
        bar = {"bar": i + 1, "start_s": round(float(start), 3), "end_s": round(end, 3)}
        if fps:
            # whole frames from cumulative positions: the frame a bar starts on is round(t*fps),
            # its length the difference to the next start, so rounding never accumulates
            bar["start_frame"] = frames_at(float(start), fps)
            bar["frames"] = frames_at(end, fps) - bar["start_frame"]
        bars.append(bar)
    phrases = {}
    for length in (1, 2, 4, 8):
        rows = []
        for i in range(0, len(bars), length):
            chunk = bars[i : i + length]
            if len(chunk) < length:
                break
            row = {"phrase": len(rows) + 1, "bar": chunk[0]["bar"], "start_s": chunk[0]["start_s"], "end_s": chunk[-1]["end_s"]}
            if fps:
                row["start_frame"] = chunk[0]["start_frame"]
                row["frames"] = frames_at(chunk[-1]["end_s"], fps) - chunk[0]["start_frame"]
            rows.append(row)
        phrases[str(length)] = rows
    out = {
        "tempo_bpm": round(tempo, 2),
        "beats_per_bar": beats_per_bar,
        "beat_s": round(60.0 / tempo, 4),
        "bar_s": round(bar_s, 4),
        "duration_s": round(duration, 3),
        "beat_times_s": [round(float(b), 3) for b in beats],
        "downbeat_times_s": [round(float(b), 3) for b in downbeats],
        "bars": bars,
        "phrases": phrases,
    }
    if fps:
        out["fps"] = fps
        out["beat_frames"] = [frames_at(float(b), fps) for b in beats]
        out["downbeat_frames"] = [frames_at(float(b), fps) for b in downbeats]
        out["bar_frames_typical"] = int(round(bar_s * fps))
        out["phrase_frames_typical"] = {str(n): int(round(n * bar_s * fps)) for n in (1, 2, 4, 8)}
    return out


def verb_beats(args, fps):
    t0 = time.monotonic()
    beats, downbeats, duration = track_beats(args.media)
    out = beat_structure(beats, downbeats, duration, fps)
    out["media"] = args.media
    out["elapsed_s"] = round(time.monotonic() - t0, 2)
    return out


ENERGY_LABELS = ((0.15, "still"), (0.35, "calm"), (0.6, "building"), (0.85, "high"), (10.0, "peak"))


def energy_label(v: float) -> str:
    for ceiling, name in ENERGY_LABELS:
        if v < ceiling:
            return name
    return "peak"


def _scale01(values: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(values, 5), np.percentile(values, 95)
    if hi - lo <= 1e-9:
        return np.zeros_like(values)
    return np.clip((values - lo) / (hi - lo), 0.0, 1.0)


def verb_energy(args, fps):
    import librosa

    t0 = time.monotonic()
    sr = 22050
    audio = decode(args.media, sr)
    if args.beats:
        pre = json.loads(Path(args.beats).read_text(encoding="utf-8"))
        bars = pre["bars"]
    else:
        beats, downbeats, duration = track_beats(args.media)
        bars = beat_structure(beats, downbeats, duration, fps)["bars"]
    hop = 512
    rms = librosa.feature.rms(y=audio, frame_length=2048, hop_length=hop)[0]
    cent = librosa.feature.spectral_centroid(y=audio, sr=sr, hop_length=hop)[0]
    onset_env = librosa.onset.onset_strength(y=audio, sr=sr, hop_length=hop)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop, units="time")
    mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=13, hop_length=hop)
    chroma = librosa.feature.chroma_stft(y=audio, sr=sr, hop_length=hop)
    times = librosa.frames_to_time(np.arange(rms.size), sr=sr, hop_length=hop)
    loud, bright, dens, feats = [], [], [], []
    for bar in bars:
        m = (times >= bar["start_s"]) & (times < bar["end_s"])
        if not m.any():
            m = np.zeros_like(times, dtype=bool)
            m[min(times.size - 1, int(np.searchsorted(times, bar["start_s"])))] = True
        loud.append(db(float(np.sqrt(np.mean(np.square(rms[m]))))))
        bright.append(float(np.mean(cent[m])))
        span = max(1e-3, bar["end_s"] - bar["start_s"])
        dens.append(float(np.sum((onsets >= bar["start_s"]) & (onsets < bar["end_s"]))) / span)
        feats.append(np.concatenate([np.mean(mfcc[:, m], axis=1) / 100.0, np.mean(chroma[:, m], axis=1)]))
    loud_a, bright_a, dens_a = np.array(loud), np.array(bright), np.array(dens)
    raw = 0.5 * _scale01(loud_a) + 0.25 * _scale01(bright_a) + 0.25 * _scale01(dens_a)
    kernel = np.ones(3) / 3.0
    smooth = np.convolve(np.pad(raw, 1, mode="edge"), kernel, mode="valid") if raw.size >= 3 else raw
    per_bar = []
    for bar, r, s, l, b, d in zip(bars, raw, smooth, loud_a, bright_a, dens_a):
        per_bar.append(dict(bar, intensity=round(float(s), 3), raw=round(float(r), 3), loudness_db=round(float(l), 1), centroid_hz=round(float(b)), onsets_per_s=round(float(d), 2)))
    # sections: a checkerboard novelty over bar features (timbre, harmony, intensity), peaks a few bars apart
    F = np.array([np.concatenate([f, [s * 3.0]]) for f, s in zip(feats, smooth)])
    n = F.shape[0]
    if n >= 4:
        Fn = F - F.mean(axis=0)
        Fn = Fn / (np.linalg.norm(Fn, axis=1, keepdims=True) + 1e-9)
        S = Fn @ Fn.T
        k = min(4, n // 2)
        kern = np.kron(np.array([[1, -1], [-1, 1]]), np.ones((k, k)))
        nov = np.zeros(n)
        for i in range(k, n - k):
            nov[i] = float(np.sum(S[i - k : i + k, i - k : i + k] * kern))
        thresh = nov.mean() + 0.5 * nov.std()
        bounds = [0]
        for i in range(1, n - 1):
            if nov[i] > thresh and nov[i] >= nov[i - 1] and nov[i] >= nov[i + 1] and i - bounds[-1] >= max(2, k):
                bounds.append(i)
        bounds.append(n)
    else:
        bounds = [0, n]
    sections = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        chunk = per_bar[a:b]
        if not chunk:
            continue
        mean_i = float(np.mean([c["intensity"] for c in chunk]))
        prev = sections[-1]["intensity"] if sections else None
        label = energy_label(mean_i)
        if label == "calm" and prev is not None and mean_i > prev + 0.1:
            label = "building"
        sec = {"section": len(sections) + 1, "bar": chunk[0]["bar"], "bars": len(chunk), "start_s": chunk[0]["start_s"], "end_s": chunk[-1]["end_s"], "intensity": round(mean_i, 3), "level": label}
        if fps:
            sec["start_frame"] = chunk[0].get("start_frame")
            sec["frames"] = frames_at(chunk[-1]["end_s"], fps) - chunk[0]["start_frame"]
        sections.append(sec)
    return {"media": args.media, "bars": per_bar, "sections": sections, "labels": [name for _, name in ENERGY_LABELS], "elapsed_s": round(time.monotonic() - t0, 2)}


# -- cuts and motion -------------------------------------------------------------------


def gray_frames(path: str, w: int = 64, h: int = 36) -> tuple[np.ndarray, float]:
    """Every frame as a small grey picture (n, h, w) in 0..1, and the file's frame rate."""
    info = probe(path)
    if not info["video"]:
        raise ToolError(f"{Path(path).name} has no video stream")
    rate = info["fps"] or 24.0
    # passthrough: the file's own frames in order, none duplicated or dropped to fit a clock
    cmd = [_ffmpeg(), "-v", "error", "-nostdin", "-i", path, "-an", "-vf", f"scale={w}:{h}", "-pix_fmt", "gray", "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=1800)
    if r.returncode != 0:
        raise ToolError("ffmpeg could not decode the video")
    raw = np.frombuffer(r.stdout, dtype=np.uint8)
    n = raw.size // (w * h)
    if n < 2:
        raise ToolError("fewer than two frames")
    return raw[: n * w * h].reshape(n, h, w).astype(np.float32) / 255.0, rate


def scene_cuts(path: str, threshold: float) -> tuple[list[int], float]:
    """Frame indices where a new shot starts: the mean absolute grey difference to the frame
    before crosses ``threshold`` (0..1) and stands well above the clip's usual motion. Exact
    to the frame, which ffmpeg's scene filter is not."""
    frames, rate = gray_frames(path)
    d = np.mean(np.abs(np.diff(frames, axis=0)), axis=(1, 2))  # d[i] is frame i+1 against frame i
    floor = float(np.median(d))
    cuts = [int(i + 1) for i, v in enumerate(d) if v >= threshold and v >= 4.0 * floor]
    return cuts, rate


def verb_cuts(args, fps):
    if not fps:
        raise ToolError("cuts needs --fps")
    info = probe(args.media)
    if not info["video"]:
        raise ToolError(f"{Path(args.media).name} has no video stream")
    t0 = time.monotonic()
    cut_frames, native = scene_cuts(args.media, args.threshold)
    if abs(native - fps) > 0.01:
        # the export is read at its own rate; the caller's fps names the sequence rate
        cut_frames = [int(round(f / native * fps)) for f in cut_frames]
    cuts = [f / fps for f in cut_frames]
    beats, downbeats, _ = track_beats(args.song)
    # the song sits under the export from song_in (in the song) at start_frame (in the export)
    shift = float(args.start_frame or 0) / fps - float(args.song_in or 0.0)
    beat_frames = np.array(sorted(set(int(round((b + shift) * fps)) for b in beats)))
    down_frames = np.array(sorted(set(int(round((b + shift) * fps)) for b in downbeats)))
    rows = []
    for t, f in zip(cuts, cut_frames):
        nb = int(beat_frames[np.argmin(np.abs(beat_frames - f))]) if beat_frames.size else None
        nd = int(down_frames[np.argmin(np.abs(down_frames - f))]) if down_frames.size else None
        rows.append({"cut_s": round(t, 3), "cut_frame": f, "beat_frame": nb, "beat_miss_frames": None if nb is None else f - nb, "downbeat_frame": nd, "downbeat_miss_frames": None if nd is None else f - nd})
    misses = [abs(r["beat_miss_frames"]) for r in rows if r["beat_miss_frames"] is not None]
    return {
        "media": args.media,
        "song": args.song,
        "fps": fps,
        "song_in_s": float(args.song_in or 0.0),
        "start_frame": int(args.start_frame or 0),
        "threshold": args.threshold,
        "cuts": rows,
        "on_beat": sum(1 for m in misses if m <= 1),
        "off_beat": [r for r in rows if r["beat_miss_frames"] is not None and abs(r["beat_miss_frames"]) > 1],
        "max_miss_frames": max(misses) if misses else None,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


def verb_motion(args, fps):
    frames, rate = gray_frames(args.media)
    n = frames.shape[0]
    diffs = np.mean(np.abs(np.diff(frames, axis=0)), axis=(1, 2))
    per_second = []
    for s in range(int(math.ceil(n / rate))):
        a, b = int(s * rate), int(min(n - 1, (s + 1) * rate))
        chunk = diffs[max(0, a - 1) : max(a, b - 1)]
        if chunk.size:
            per_second.append({"second": s, "motion": round(float(np.mean(chunk)), 4)})
    return {"media": args.media, "fps": rate, "frames": n, "motion_mean": round(float(np.mean(diffs)), 4), "motion_p90": round(float(np.percentile(diffs, 90)), 4), "per_second": per_second}


# -- main ----------------------------------------------------------------------------


VERBS = {
    "transcribe": verb_transcribe,
    "voice": verb_voice,
    "emphasis": verb_emphasis,
    "levels": verb_levels,
    "beats": verb_beats,
    "energy": verb_energy,
    "cuts": verb_cuts,
    "motion": verb_motion,
}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="run.py", description="THM audio tool: measure, print JSON, change nothing")
    sub = ap.add_subparsers(dest="verb", required=True)

    def common(p, fps_help="also report times in whole frames at this rate"):
        p.add_argument("--fps", type=float, default=None, help=fps_help)
        p.add_argument("--scratch", default=None, help="where files go (the CLI names it)")
        p.add_argument("--json", action="store_true", help="accepted for symmetry; output is always JSON")

    p = sub.add_parser("transcribe", help="words with times and confidence; --expect adds WER and a word diff")
    p.add_argument("media")
    p.add_argument("--expect", default=None)
    p.add_argument("--model", default=DEFAULT_WHISPER, help=f"faster-whisper size; default {DEFAULT_WHISPER}")
    p.add_argument("--language", default=None, help="ISO code; default: detected")
    common(p)
    p = sub.add_parser("voice", help="ECAPA similarity of the line (or a window of it) to each reference voice")
    p.add_argument("media")
    p.add_argument("--ref", action="append", required=True, help="reference media, repeatable or comma-separated")
    p.add_argument("--window", default=None, help="s0:s1 in the line")
    common(p)
    p = sub.add_parser("emphasis", help="which word carries the stress, against the CAPS word(s) in --expect")
    p.add_argument("media")
    p.add_argument("--expect", required=True)
    p.add_argument("--model", default=DEFAULT_WHISPER)
    p.add_argument("--language", default=None)
    common(p)
    p = sub.add_parser("levels", help="voice-band loudness per window, P90, clicks and pops, edge bursts")
    p.add_argument("media")
    common(p)
    p = sub.add_parser("beats", help="tempo, beats, downbeats, bars and phrases; frames with --fps")
    p.add_argument("media")
    common(p)
    p = sub.add_parser("energy", help="per-bar intensity 0..1 and sections with a level label")
    p.add_argument("media")
    p.add_argument("--beats", default=None, help="a beats JSON from this tool, to skip tracking again")
    common(p)
    p = sub.add_parser("cuts", help="scene cuts in an export against the song's beats")
    p.add_argument("media")
    p.add_argument("--song", required=True)
    p.add_argument("--song-in", dest="song_in", type=float, default=0.0, help="where in the song the layer starts (s)")
    p.add_argument("--start-frame", dest="start_frame", type=int, default=0, help="the export frame the song starts on")
    p.add_argument("--threshold", type=float, default=SCENE_THRESHOLD, help="mean grey frame-to-frame change (0..1) that counts as a cut")
    common(p, "the export's frame rate (required)")
    p = sub.add_parser("motion", help="mean frame-to-frame change per second")
    p.add_argument("media")
    common(p)
    return ap


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code in (0, None) else 2
    scratch = Path(args.scratch) if args.scratch else Path.cwd()
    try:
        scratch.mkdir(parents=True, exist_ok=True)
        result = VERBS[args.verb](args, args.fps)
        result["verb"] = args.verb
        result["scratch"] = str(scratch)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except ToolError as exc:
        print(json.dumps({"verb": args.verb, "error": str(exc)}, ensure_ascii=True))
        return 1
    except Exception as exc:  # noqa: BLE001 - the failure is the result
        print(json.dumps({"verb": args.verb, "error": f"{exc.__class__.__name__}: {exc}"}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
