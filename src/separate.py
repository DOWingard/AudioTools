#!/usr/bin/env python
"""
Three-stage audio separation pipeline:
  Stage 1 (Demucs):      input → vocals, drums, bass, other
  Stage 2 (LARS):        drums → kick, snare, toms, hihat, cymbals
  Stage 3 (Gate+Slice):  each drum stem → one-shot WAV samples
                           A) onset detection (librosa)
                           B) RMS loudness gating
                           C) look-ahead windowed gate (fast attack, slow release)
                           D) transient slice per hit
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torchaudio

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_HERE = Path(__file__).resolve().parent.parent  # project root
_DEMUCS_DIR = _HERE / "demucs"
_LARS_MODELS_DIR = _HERE / "LARS" / "drums_demix" / "DrumsDemixUtils" / "DrumsDemixModels"

# Inject demucs source onto path before importing
sys.path.insert(0, str(_DEMUCS_DIR))
from demucs.api import Separator as _DemucsSeparator  # noqa: E402
from demucs.audio import save_audio as _demucs_save   # noqa: E402

# --------------------------------------------------------------------------- #
# LARS helpers
# --------------------------------------------------------------------------- #
_LARS_N_FFT  = 4096
_LARS_HOP    = 1024
_LARS_WIN    = 4096
_LARS_SR     = 44100   # models were trained at 44100 Hz

_LARS_STEMS  = ["kick", "snare", "toms", "hihat", "cymbals"]

# Natural decay release time per drum stem (ms)
_STEM_RELEASE_MS: dict[str, float] = {
    "kick":    600.0,
    "snare":   400.0,
    "toms":    500.0,
    "hihat":   200.0,
    "cymbals": 700.0,
}

# High-pass cutoff (Hz) applied to the analysis signal only (onset detection +
# RMS evaluation).  Saved audio is always full-bandwidth.
_ANALYSIS_HIGHPASS_HZ: dict[str, float] = {
    "cymbals": 1000.0,   # ignore sub-1 kHz content when picking the best hit
    "toms":     100.0,   # ignore sub-100 Hz content when picking the best hit
    "hihat":   5000.0,   # ignore sub-5 kHz content when picking the best hit
}


def _highpass(signal: np.ndarray, cutoff_hz: float, sr: int, order: int = 4) -> np.ndarray:
    """Zero-phase 4th-order Butterworth high-pass filter."""
    from scipy.signal import butter, sosfiltfilt
    sos = butter(order, cutoff_hz / (sr / 2.0), btype="high", output="sos")
    return sosfiltfilt(sos, signal).astype(np.float32)


def _load_lars_models(device: str) -> dict:
    models = {}
    for stem in _LARS_STEMS:
        path = _LARS_MODELS_DIR / f"my_scripted_module_{stem}.pt"
        model = torch.jit.load(str(path), map_location=device)
        model.eval()
        models[stem] = model
    return models


def _stft(audio: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """(2, N) → magnitude (2, F, T), phase (2, F, T)"""
    window = torch.hann_window(_LARS_WIN, periodic=True, device=audio.device)
    S = torch.stft(
        audio,
        n_fft=_LARS_N_FFT,
        hop_length=_LARS_HOP,
        win_length=_LARS_WIN,
        window=window,
        center=True,
        return_complex=True,
    )
    return S.abs(), S.angle()


def _istft(magnitude: torch.Tensor, phase: torch.Tensor, length: int) -> torch.Tensor:
    """magnitude (2, F, T), phase (2, F, T) → (2, N)"""
    window = torch.hann_window(_LARS_WIN, periodic=True, device=magnitude.device)
    S = torch.polar(magnitude, phase)
    return torch.istft(
        S,
        n_fft=_LARS_N_FFT,
        hop_length=_LARS_HOP,
        win_length=_LARS_WIN,
        window=window,
        center=True,
        length=length,
    )


# --------------------------------------------------------------------------- #
# Stage 3 helpers
# --------------------------------------------------------------------------- #

def _build_gate_envelope(
    n_total: int,
    look_ahead: int,
    hold: int,
    release: int,
) -> np.ndarray:
    """
    Look-ahead windowed gate envelope of length `n_total`.

    Layout (relative to start of window, which begins `look_ahead` samples
    before the detected onset):

      [0 : look_ahead]               — linear ramp 0 → 1  (attack)
      [look_ahead : look_ahead+hold] — held at 1.0
      [look_ahead+hold : end]        — exponential decay toward 0  (release)

    The gate is fully open exactly at the onset, preserving the transient.
    The slow exponential release preserves the natural drum decay.
    """
    env = np.zeros(n_total, dtype=np.float32)

    # Attack — ramps up during the look-ahead period so the gate is fully
    # open the instant the transient arrives.
    atk_end = min(look_ahead, n_total)
    if atk_end > 0:
        env[:atk_end] = np.linspace(0.0, 1.0, atk_end, dtype=np.float32)

    # Hold
    hold_end = min(atk_end + hold, n_total)
    env[atk_end:hold_end] = 1.0

    # Exponential release: reaches ~−40 dB (0.01) at the end of the window.
    n_rel = n_total - hold_end
    if n_rel > 0:
        tau = 4.6 / n_rel          # e^(−tau*n_rel) ≈ 0.01
        env[hold_end:] = np.exp(-np.arange(n_rel, dtype=np.float32) * tau)

    return env


# --------------------------------------------------------------------------- #
# Stage 1: Demucs
# --------------------------------------------------------------------------- #
def stage1_demucs(
    input_path: Path,
    output_dir: Path,
    device: str = "cpu",
) -> tuple[dict[str, torch.Tensor], int]:
    """
    Separate *input_path* into four stems using Demucs (htdemucs).
    Saves each stem as a WAV and returns (stems_dict, samplerate).
    """
    print(f"[Stage 1] Demucs separation: {input_path.name}")
    sep = _DemucsSeparator(model="htdemucs", device=device, progress=True)
    _, stems = sep.separate_audio_file(input_path)

    _STEM_RENAME = {"bass": "sub", "other": "midbass"}

    output_dir.mkdir(parents=True, exist_ok=True)
    renamed: dict[str, torch.Tensor] = {}
    for name, audio in stems.items():
        label = _STEM_RENAME.get(name, name)
        out = output_dir / f"{label}.wav"
        _demucs_save(audio, str(out), samplerate=sep.samplerate)
        print(f"  saved → {out}")
        renamed[label] = audio

    return renamed, sep.samplerate


# --------------------------------------------------------------------------- #
# Stage 2: LARS drum separation
# --------------------------------------------------------------------------- #
def stage2_lars(
    drums_audio: torch.Tensor,
    drums_sr: int,
    output_dir: Path,
    device: str = "cpu",
) -> dict[str, torch.Tensor]:
    """
    Separate a drums-only stem into five kit components using LARS.

    *drums_audio*: (2, N) float32 tensor at *drums_sr* Hz.
    Returns dict stem_name → (2, N) tensor at 44100 Hz.
    """
    print("[Stage 2] LARS drum separation")
    output_dir.mkdir(parents=True, exist_ok=True)

    if drums_sr != _LARS_SR:
        print(f"  resampling drums {drums_sr} Hz → {_LARS_SR} Hz")
        drums_audio = torchaudio.functional.resample(drums_audio, drums_sr, _LARS_SR)

    # Ensure stereo (2, N)
    if drums_audio.ndim == 1:
        drums_audio = drums_audio.unsqueeze(0).expand(2, -1)
    elif drums_audio.shape[0] == 1:
        drums_audio = drums_audio.expand(2, -1)

    drums_audio = drums_audio.to(device)
    n_samples = drums_audio.shape[-1]

    mag, phase = _stft(drums_audio)      # (2, 2049, T)
    mag_batch  = mag.unsqueeze(0)        # (1, 2, 2049, T)

    print("  loading LARS models …")
    models = _load_lars_models(device)

    results: dict[str, torch.Tensor] = {}
    for stem, model in models.items():
        with torch.no_grad():
            out_mag = model(mag_batch)   # (1, 2, 2049, T)
        out_mag   = out_mag.squeeze(0)   # (2, 2049, T)
        audio_out = _istft(out_mag, phase, length=n_samples).cpu()
        results[stem] = audio_out

        out_path = output_dir / f"drums_{stem}.wav"
        torchaudio.save(str(out_path), audio_out, _LARS_SR)
        print(f"  saved → {out_path}")

    return results


# --------------------------------------------------------------------------- #
# Stage 3: gate + slice → one-shot samples
# --------------------------------------------------------------------------- #
def stage3_slice(
    lars_stems: dict[str, torch.Tensor],
    sr: int,
    output_dir: Path,
    look_ahead_ms:     float = 10.0,
    hold_ms:           float = 20.0,
    rms_threshold_db:  float = -42.0,
    min_gap_ms:        float = 80.0,
    normalize:         bool  = True,
) -> dict[str, Path]:
    """
    For each LARS drum stem produce per-hit one-shot WAV files.

    Step A — Onset detection via librosa (backtracked, RMS-energy based).
    Step B — RMS loudness check: hits below *rms_threshold_db* are discarded.
    Step C — Look-ahead windowed gate applied around each onset:
                fast linear attack (ramps up over the look-ahead window so
                the gate is fully open at the transient), short hold, then
                slow exponential release that preserves the natural decay.
    Step D — Gate and slice each hit in memory, keeping only the loudest one.
              Saves a single ``<stem>_oneshot.wav`` per stem in *output_dir*.

    Returns dict  stem_name → Path  of the one saved one-shot per stem.
    """
    import librosa  # imported here to avoid numba startup cost when not used

    output_dir.mkdir(parents=True, exist_ok=True)
    all_paths: dict[str, Path] = {}

    hop_length = 256   # librosa onset detection hop (≈ 5.8 ms at 44100 Hz)

    for stem_name, audio in lars_stems.items():  # audio: (2, N) float32
        print(f"  [Stage 3] {stem_name}")

        # ── timing constants (samples) ────────────────────────────────────── #
        release_ms  = _STEM_RELEASE_MS.get(stem_name, 350.0)
        look_ahead  = int(look_ahead_ms / 1000 * sr)
        hold        = int(hold_ms       / 1000 * sr)
        release     = int(release_ms    / 1000 * sr)
        window_len  = look_ahead + hold + release

        min_gap_samples = int(min_gap_ms / 1000 * sr)
        wait_frames     = max(1, int(min_gap_ms / 1000 * sr / hop_length))
        rms_linear      = 10 ** (rms_threshold_db / 20.0)

        # ── Step A: onset detection ───────────────────────────────────────── #
        mono = audio.mean(0).numpy().astype(np.float32)  # (N,) full-bandwidth
        n_total = len(mono)

        # Band-limited analysis signal for onset detection and RMS evaluation.
        # Filters out low-frequency content that is irrelevant to the stem
        # (e.g. sub-1 kHz bleed on cymbals, sub-100 Hz rumble on toms) so
        # that the loudest-hit decision reflects only the target frequency range.
        if stem_name in _ANALYSIS_HIGHPASS_HZ:
            analysis_mono = _highpass(mono, _ANALYSIS_HIGHPASS_HZ[stem_name], sr)
        else:
            analysis_mono = mono

        onset_frames = librosa.onset.onset_detect(
            y=analysis_mono,
            sr=sr,
            hop_length=hop_length,
            backtrack=True,      # walk back from peak to true onset
            wait=wait_frames,    # coarse minimum gap in frames
            units="frames",
        )
        onset_samples = librosa.frames_to_samples(onset_frames, hop_length=hop_length)

        # Strict minimum-gap filter (sample-accurate)
        filtered: list[int] = []
        last = -min_gap_samples
        for o in onset_samples:
            if o - last >= min_gap_samples:
                filtered.append(int(o))
                last = o

        best_rms   = -1.0
        best_audio: np.ndarray | None = None
        n_candidates = 0

        for onset in filtered:
            # ── Step B: RMS loudness (measured on band-limited signal) ────── #
            rms_len = min(int(0.1 * sr), n_total - onset)   # 100 ms window
            if rms_len <= 0:
                continue
            hit_rms = float(np.sqrt(np.mean(analysis_mono[onset: onset + rms_len] ** 2)))
            if hit_rms < rms_linear:
                continue  # too quiet — discard

            n_candidates += 1

            # ── Step C: look-ahead windowed gate ─────────────────────────── #
            win_start = max(0, onset - look_ahead)
            win_end   = min(n_total, win_start + window_len)
            slice_len = win_end - win_start

            # Actual look-ahead available (may be less at track start)
            actual_look_ahead = onset - win_start

            gate = _build_gate_envelope(
                n_total   = slice_len,
                look_ahead= actual_look_ahead,
                hold      = hold,
                release   = release,
            )

            # ── Step D: gate + slice in memory, keep only loudest ─────────── #
            stereo_slice = audio[:, win_start:win_end].numpy()  # (2, slice_len)
            gated = (stereo_slice * gate[np.newaxis, :]).astype(np.float32)

            if hit_rms > best_rms:
                best_rms   = hit_rms
                best_audio = gated

        # Save the single loudest hit
        if best_audio is not None:
            if normalize:
                peak = float(np.abs(best_audio).max())
                if peak > 1e-6:
                    best_audio = best_audio * (0.9 / peak)

            best_path = output_dir / f"{stem_name}_oneshot.wav"
            torchaudio.save(
                str(best_path),
                torch.from_numpy(np.ascontiguousarray(best_audio)),
                sr,
            )
            print(f"    {n_candidates} hits evaluated → kept loudest: {best_path.name}")
            all_paths[stem_name] = best_path
        else:
            print(f"    no hits above threshold for {stem_name}")

    return all_paths


# --------------------------------------------------------------------------- #
# Full pipeline
# --------------------------------------------------------------------------- #
def run_pipeline(
    input_path: str | Path,
    output_dir: str | Path,
    device: str = "cpu",
) -> dict[str, Path | list[Path]]:
    """
    Run the full three-stage pipeline on *input_path*.

    Returns a flat dict of all output files:
      "bass", "drums", "other", "vocals"  → single Path
      "drums_<stem>"                       → single Path  (full LARS stem)
      "oneshot_<stem>"                     → Path  (loudest hit only)
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    demucs_dir   = output_dir / "demucs"
    lars_dir     = output_dir / "lars"
    oneshots_dir = output_dir / "oneshots"

    # Stage 1 ----------------------------------------------------------------
    demucs_stems, demucs_sr = stage1_demucs(input_path, demucs_dir, device)

    # Stage 2 ----------------------------------------------------------------
    lars_stems = stage2_lars(demucs_stems["drums"], demucs_sr, lars_dir, device)

    # Stage 3 ----------------------------------------------------------------
    print("[Stage 3] Gate + slice → one-shot samples")
    oneshot_paths = stage3_slice(lars_stems, _LARS_SR, oneshots_dir)

    # ── Collect all output paths ─────────────────────────────────────────── #
    all_files: dict[str, Path | list[Path]] = {}

    for name in demucs_stems:
        all_files[name] = demucs_dir / f"{name}.wav"

    for name in lars_stems:
        all_files[f"drums_{name}"] = lars_dir / f"drums_{name}.wav"

    for stem_name, path in oneshot_paths.items():
        all_files[f"oneshot_{stem_name}"] = path

    print("\n=== Output summary ===")
    for label, val in sorted(all_files.items()):
        print(f"  {label:28s}  {val}")

    return all_files


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(
        description="Three-stage source separation: Demucs → LARS → one-shots"
    )
    parser.add_argument("input", help="Input audio file (mp3/wav/flac/…)")
    parser.add_argument(
        "-o", "--output-dir", default=None,
        help="Output directory (default: <input_stem>_separated/)",
    )
    parser.add_argument(
        "--device",
        default="cuda" if torch.cuda.is_available() else "cpu",
        help="Torch device (default: cuda if available, else cpu)",
    )
    parser.add_argument(
        "--look-ahead-ms", type=float, default=10.0,
        help="Gate look-ahead before onset in ms (default: 10)",
    )
    parser.add_argument(
        "--hold-ms", type=float, default=20.0,
        help="Gate hold time after onset in ms (default: 20)",
    )
    parser.add_argument(
        "--threshold-db", type=float, default=-42.0,
        help="RMS threshold to reject quiet hits in dBFS (default: -42)",
    )
    parser.add_argument(
        "--min-gap-ms", type=float, default=80.0,
        help="Minimum gap between onsets in ms (default: 80)",
    )
    parser.add_argument(
        "--no-normalize", action="store_true",
        help="Skip peak-normalisation of one-shot slices",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        parser.error(f"Input file not found: {input_path}")

    out_dir = Path(args.output_dir) if args.output_dir else (
        input_path.parent / f"{input_path.stem}_separated"
    )

    run_pipeline(input_path, out_dir, device=args.device)


if __name__ == "__main__":
    main()
