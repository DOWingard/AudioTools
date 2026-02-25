#!/usr/bin/env python
"""
Vocal extractor — extracts the vocal stem from an audio file using Demucs (htdemucs).
"""

import argparse
import sys
from pathlib import Path

import torch

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_HERE = Path(__file__).resolve().parent.parent  # project root
_DEMUCS_DIR = _HERE / "demucs"

sys.path.insert(0, str(_DEMUCS_DIR))
from demucs.api import Separator as _DemucsSeparator  # noqa: E402
from demucs.audio import save_audio as _demucs_save   # noqa: E402


def separate_stems(
    input_path: Path,
    output_dir: Path,
    device: str = "cpu",
) -> dict[str, Path]:
    """
    Separate *input_path* into all four Demucs stems.
    Saves vocals.wav, drums.wav, bass.wav, other.wav to *output_dir*.
    Returns a dict of stem name → Path.
    """
    print(f"[Demucs] Separating: {input_path.name}")
    sep = _DemucsSeparator(model="htdemucs", device=device, progress=True)
    _, stems = sep.separate_audio_file(input_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, Path] = {}
    for name, audio in stems.items():
        out = output_dir / f"{name}.wav"
        _demucs_save(audio, str(out), samplerate=sep.samplerate)
        print(f"  saved → {out}")
        saved[name] = out

    return saved


def main():
    parser = argparse.ArgumentParser(
        description="Extract vocal stem from an audio file using Demucs"
    )
    parser.add_argument("input", help="Input audio file (mp3/wav/flac/…)")
    parser.add_argument(
        "-o", "--output-dir", default=None,
        help="Output directory (default: <input_stem>_stems/ next to input file)",
    )
    parser.add_argument(
        "--device",
        default="cuda" if torch.cuda.is_available() else "cpu",
        help="Torch device (default: cuda if available, else cpu)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        parser.error(f"Input file not found: {input_path}")

    out_dir = Path(args.output_dir) if args.output_dir else (
        input_path.parent / f"{input_path.stem}_stems"
    )

    saved = separate_stems(input_path, out_dir, device=args.device)
    print(f"\nDone. Stems saved to: {out_dir}")
    for name, path in sorted(saved.items()):
        print(f"  {name:8s} → {path}")


if __name__ == "__main__":
    main()
