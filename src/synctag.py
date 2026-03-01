#!/usr/bin/env python
"""
SyncTag AI — End-to-End Sync Licensing Metadata Pipeline

Stages:
  1. Ingestion & Preprocessing  — load audio, compute duration
  2. Stem Separation            — Demucs: vocals / drums / bass / other
  3. Embedding Extraction       — M2D-CLAP audio embeddings for mix + stems
  4. Zero-Shot Classification   — cosine similarity vs taxonomy text prompts

Output: structured dict (also serialisable to JSON) containing:
  - audio_info  : duration, sample rate, mix RMS
  - stem_info   : per-stem RMS energy + presence flag
  - tags        : top genre / mood / instruments / tempo (constrained vocabulary)
  - scores      : full ranked list per category
  - metadata    : DISCO-ready metadata dict (Title/Artist/Album injected by caller)
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
from dotenv import load_dotenv

load_dotenv()

from src.embedder import AudioEmbedder
from src.separate import separate_stems
from src.taxonomy import GENRES, INSTRUMENTS, MOODS, TEMPOS, TRACK_TYPES

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# RMS threshold: stems below this are considered absent (silence/bleed only)
_STEM_PRESENCE_THRESHOLD = 0.005

# Top-k for the final `tags` summary (scores dict always has the full list)
_TOP_GENRE = 3
_TOP_MOOD = 5
_TOP_INSTRUMENT = 8

# Instrument groups — drives which stem is blended for classification
_DRUM_INSTRUMENTS = frozenset({"Acoustic Drums", "Electronic Drums", "Percussion", "808"})
_VOCAL_INSTRUMENTS = frozenset({"Female Vocals", "Male Vocals", "Choir"})
_BASS_INSTRUMENTS = frozenset({"Bass Guitar", "Synth Bass"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity; safe against zero-norm vectors."""
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / (denom + 1e-8))


def _get_duration_ffprobe(path: str) -> float:
    """Return audio duration in seconds via ffprobe (no Python audio deps)."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", path,
            ],
            capture_output=True, text=True, check=True,
        )
        return float(json.loads(result.stdout).get("format", {}).get("duration", 0.0))
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# SyncTagger
# ---------------------------------------------------------------------------

class SyncTagger:
    """
    Orchestrates the 4-stage SyncTag AI pipeline.

    Parameters
    ----------
    weight_file : str, optional
        Path to the M2D-CLAP checkpoint. Defaults to the project default.
    """

    def __init__(
        self,
        weight_file: Optional[str] = None,
    ):
        kwargs = {"weight_file": weight_file} if weight_file else {}
        self.embedder = AudioEmbedder(**kwargs)
        # Cache text embeddings — encoding is the most expensive per-label op
        self._text_cache: dict[str, np.ndarray] = {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(self, audio_path: "str | Path") -> dict:
        """
        Run the full SyncTag pipeline on a single audio file.

        Returns
        -------
        dict with keys: file, title, audio_info, stem_info, tags, scores, metadata
        """
        audio_path = Path(audio_path).resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio not found: {audio_path}")

        print(f"\n[SyncTag] ▶ {audio_path.name}")

        # Stage 1
        print("[Stage 1/4] Preprocessing…")
        audio, audio_info = self._preprocess(audio_path)

        # Stage 2
        print("[Stage 2/4] Separating stems (Demucs)…")
        tmp_dir = Path(tempfile.mkdtemp(prefix="synctag_"))
        stems = self._separate(audio_path, tmp_dir)
        stem_audio = {name: self.embedder.load_audio(str(p)) for name, p in stems.items()}
        stem_info = self._analyze_stems(stem_audio)

        # Stage 3
        print("[Stage 3/4] Extracting M2D-CLAP embeddings…")
        embeddings = self._extract_embeddings(audio, stem_audio)

        # Stage 4
        print("[Stage 4/4] Zero-shot classification…")
        classification = self._classify(embeddings, stem_info)

        metadata = self._build_metadata(classification, stem_info, audio_info)

        return {
            "file": str(audio_path),
            "title": audio_path.stem,
            "audio_info": audio_info,
            "stem_info": stem_info,
            "tags": {
                "genre": [r["tag"] for r in classification["genre"][:_TOP_GENRE]],
                "mood": [r["tag"] for r in classification["mood"][:_TOP_MOOD]],
                "instruments": [r["tag"] for r in classification["instruments"][:_TOP_INSTRUMENT]],
                "tempo": classification["tempo"][0]["tag"],
            },
            "scores": classification,
            "metadata": metadata,
        }

    # ------------------------------------------------------------------ #
    # Stage 1 — Preprocessing
    # ------------------------------------------------------------------ #

    def _preprocess(self, audio_path: Path) -> tuple:
        """Load audio via ffmpeg → 16 kHz mono float32. Return (waveform, info)."""
        audio = self.embedder.load_audio(str(audio_path))
        duration = _get_duration_ffprobe(str(audio_path)) or len(audio) / 16_000
        rms_mix = float(np.sqrt(np.mean(audio ** 2)))
        info = {
            "duration": round(duration, 2),
            "sample_rate": 16_000,
            "num_samples": len(audio),
            "rms_mix": round(rms_mix, 6),
        }
        print(f"  Duration : {duration:.1f} s  |  samples : {len(audio):,}")
        return audio, info

    # ------------------------------------------------------------------ #
    # Stage 2 — Stem Separation
    # ------------------------------------------------------------------ #

    def _separate(self, audio_path: Path, out_dir: Path) -> dict:
        """Run Demucs and return {stem_name: Path} dict."""
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        return separate_stems(audio_path, out_dir, device=device)

    def _analyze_stems(self, stem_audio: dict) -> dict:
        """Compute per-stem RMS energy and derive a presence flag."""
        info = {}
        for name, audio in stem_audio.items():
            rms = float(np.sqrt(np.mean(audio ** 2)))
            info[name] = {
                "energy_rms": round(rms, 6),
                "present": rms > _STEM_PRESENCE_THRESHOLD,
            }
        has_vocals = info.get("vocals", {}).get("present", False)
        summary = "  " + "  ".join(
            f"{k}={v['energy_rms']:.4f}({'✓' if v['present'] else '✗'})"
            for k, v in info.items()
        )
        print(summary)
        print(f"  Vocal track : {'YES' if has_vocals else 'NO (Instrumental)'}")
        return info

    # ------------------------------------------------------------------ #
    # Stage 3 — Embedding Extraction
    # ------------------------------------------------------------------ #

    def _extract_embeddings(self, full_audio: np.ndarray, stem_audio: dict) -> dict:
        """Embed full mix + each stem. Returns {name: np.ndarray (768,)}."""
        embeddings = {"mix": self.embedder.embed(full_audio)}
        for name, audio in stem_audio.items():
            embeddings[name] = self.embedder.embed(audio)
            print(f"  embedded [{name}]")
        return embeddings

    # ------------------------------------------------------------------ #
    # Stage 4 — Zero-Shot Classification
    # ------------------------------------------------------------------ #

    def _encode_text(self, text: str) -> np.ndarray:
        """Encode a text label, with in-process caching."""
        if text not in self._text_cache:
            self._text_cache[text] = self.embedder.encode_text(text)
        return self._text_cache[text]

    def _rank(self, audio_emb: np.ndarray, labels: list, top_k: Optional[int] = None) -> list:
        """Score all labels against audio_emb and return sorted dicts."""
        scored = [
            {"tag": label, "score": round(_cosine_similarity(audio_emb, self._encode_text(label)), 4)}
            for label in labels
        ]
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k] if top_k else scored

    def _classify(self, embeddings: dict, stem_info: dict) -> dict:
        """Run zero-shot classification across all taxonomy categories."""
        mix = embeddings["mix"]

        print("  Classifying genres…")
        genres = self._rank(mix, GENRES)

        print("  Classifying moods…")
        moods = self._rank(mix, MOODS)

        print("  Classifying tempo…")
        tempos = self._rank(mix, TEMPOS, top_k=1)

        print("  Classifying instruments (stem-aware)…")
        instruments = self._classify_instruments(embeddings, stem_info)

        print("  Classifying track type…")
        track_types = self._rank(mix, TRACK_TYPES, top_k=1)

        return {
            "genre": genres,
            "mood": moods,
            "tempo": tempos,
            "instruments": instruments,
            "track_type": track_types,
        }

    def _classify_instruments(self, embeddings: dict, stem_info: dict) -> list:
        """
        Classify instruments using stem-specific embeddings where available.

        Each instrument group is compared against its most relevant stem
        (blended 60/40 with the full mix for global context). Vocal instruments
        are suppressed when the vocals stem energy is below the presence threshold.
        """
        mix = embeddings["mix"]
        vocals_present = stem_info.get("vocals", {}).get("present", False)

        results = []
        for label in INSTRUMENTS:
            # Suppress vocal instruments when track is instrumental
            if label in _VOCAL_INSTRUMENTS and not vocals_present:
                continue

            # Pick the most relevant stem for this instrument group
            if label in _DRUM_INSTRUMENTS:
                stem_emb = embeddings.get("drums", mix)
            elif label in _VOCAL_INSTRUMENTS:
                stem_emb = embeddings.get("vocals", mix)
            elif label in _BASS_INSTRUMENTS:
                stem_emb = embeddings.get("bass", mix)
            else:
                # Melodic / harmonic instruments → "other" stem + mix
                stem_emb = embeddings.get("other", mix)

            # Weighted blend: stem gives instrument clarity, mix anchors context
            blend = 0.6 * stem_emb + 0.4 * mix
            score = _cosine_similarity(blend, self._encode_text(label))
            results.append({"tag": label, "score": round(score, 4)})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    # ------------------------------------------------------------------ #
    # Metadata Builder
    # ------------------------------------------------------------------ #

    def _build_metadata(self, classification: dict, stem_info: dict, audio_info: dict) -> dict:
        """
        Derive DISCO-ready metadata dict from classification results.
        Title, Artist, and Album are left empty; the API caller injects them.
        """
        top_genres = [r["tag"] for r in classification["genre"][:_TOP_GENRE]]
        top_moods = [r["tag"] for r in classification["mood"][:_TOP_MOOD]]
        top_instruments = [r["tag"] for r in classification["instruments"][:_TOP_INSTRUMENT]]
        tempo = classification["tempo"][0]["tag"] if classification["tempo"] else "Medium"
        track_type = classification["track_type"][0]["tag"] if classification.get("track_type") else ""

        has_vocals = stem_info.get("vocals", {}).get("present", False)
        vocal = "Vocal" if has_vocals else "Instrumental"

        rms_mix = audio_info.get("rms_mix", 0.0)
        energy = min(10, max(1, round(rms_mix * 50)))

        return {
            "Title": "",
            "Artist": "",
            "Album": "",
            "Genre": top_genres[0] if top_genres else "",
            "Mood": top_moods[:3],
            "Instruments": top_instruments,
            "Tempo": tempo,
            "Energy": energy,
            "Vocal": vocal,
            "Track_Type": track_type,
            "BPM": None,
            "ISRC": "",
            "Comments": "",
            "Composer": "",
            "Year": None,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="SyncTag AI — Auto-tag an audio file for sync licensing"
    )
    parser.add_argument("input", help="Audio file to analyze (MP3 / WAV / FLAC / …)")
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Write the metadata JSON to this file path",
    )
    parser.add_argument(
        "-d", "--output-dir",
        default=None,
        help=(
            "Directory for CSV sidecar and tagged audio copy "
            "(default: same directory as input file)"
        ),
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip writing the CSV sidecar file",
    )
    parser.add_argument(
        "--no-id3",
        action="store_true",
        help="Skip embedding ID3v2 tags into the audio file",
    )
    parser.add_argument(
        "--isrc",
        default=None,
        help="ISRC code to embed (e.g. GB-ABC-25-00001). Assigned at distribution time.",
    )
    args = parser.parse_args()

    tagger = SyncTagger()
    result = tagger.run(args.input)

    if args.isrc:
        result.setdefault("metadata", {})["ISRC"] = args.isrc.strip()

    output_json = json.dumps(result, indent=2, default=str)
    print("\n" + "=" * 60)
    print(output_json)

    audio_path = Path(args.input).resolve()
    out_dir = Path(args.output_dir).resolve() if args.output_dir else audio_path.parent
    stem = audio_path.stem

    # JSON
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"[SyncTag] JSON  → {args.output}")

    # CSV sidecar
    if not args.no_csv:
        from src.export import write_csv_sidecar
        csv_path = out_dir / f"{stem}.csv"
        write_csv_sidecar(result, csv_path)
        print(f"[SyncTag] CSV   → {csv_path}")

    # ID3v2 tags embedded in-place
    if not args.no_id3:
        from src.export import write_id3_tags
        try:
            write_id3_tags(audio_path, result)
            print(f"[SyncTag] ID3   → {audio_path} (tagged in-place)")
        except Exception as exc:
            print(f"[SyncTag] ID3 WARNING: {exc}")


if __name__ == "__main__":
    main()
