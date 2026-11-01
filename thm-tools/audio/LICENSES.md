# Licenses: the audio tool

Checked 2026-10-18 (docs/design/audio-implementation.md, section 2b). Nothing here ships in the
THM download; the venv and the models are installed per machine. The distro's third-party
notices file gains these entries when the installer lands (section 8).

| Component | Role | License |
|---|---|---|
| faster-whisper | speech to text with word times | MIT |
| Systran faster-whisper weights (`small`, `medium`) | the Whisper models, CTranslate2 format | MIT |
| CTranslate2 | the inference engine behind faster-whisper | MIT |
| librosa | onsets, spectral features, segmentation | ISC |
| python-soundfile | audio file I/O | BSD-3-Clause |
| libsndfile (bundled by soundfile) | audio codecs | LGPL-2.1 |
| Parselmouth (praat-parselmouth) | pitch and intensity (Praat) | GPL-3.0 |
| SpeechBrain | speaker embeddings | Apache-2.0 |
| speechbrain/spkrec-ecapa-voxceleb weights | the ECAPA-TDNN speaker model | Apache-2.0 |
| jiwer | word error rate and alignment | Apache-2.0 |
| beat_this (code) | beat and downbeat tracking | MIT |
| beat_this `final0` checkpoint | the beat tracker weights | MIT |
| PyTorch, torchaudio (CPU wheels) | tensor runtime | BSD-3-Clause |
| numpy | arrays | BSD-3-Clause |

Not used, on purpose: audeering age/gender models (CC BY-NC-SA) and madmom models
(non-commercial).

Parselmouth is GPL-3.0: it runs as a separate process in this tool's own venv, invoked by
`thm-agent/cli.py tool audio ...`; nothing of it is linked into THM.
