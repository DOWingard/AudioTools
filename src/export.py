"""
SyncTag AI — Output Exporters

Two industry-standard output formats produced after every pipeline run:

  1. CSV Sidecar  — single-row file mapping filename → all tags.
                   Used by sync agencies for bulk CMS import.

  2. ID3v2 Tags   — metadata embedded directly into the audio file.
                   Tags travel with the file when downloaded / dragged into
                   iTunes, Spotify for Artists, DISCO, etc.

Supported audio containers for ID3 embedding:
  MP3, WAV (ID3 chunk), AIFF  → native ID3v2 frames
  FLAC                        → Vorbis Comment equivalents
  AAC, M4A                    → ID3v2 or MP4 tags
"""

import csv
from pathlib import Path
from typing import Union

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _str(v) -> str:
    """Coerce any value to a non-None string."""
    return str(v) if v is not None else ""


def _join(v) -> str:
    """Join a list to a semicolon-separated string, or pass through scalars."""
    if isinstance(v, list):
        return "; ".join(_str(x) for x in v)
    return _str(v)


def _meta_and_tags(result: dict) -> tuple[dict, dict, dict]:
    """Unpack the three sub-dicts used by both exporters."""
    llm = result.get("llm", {})
    meta = llm.get("metadata", {})
    tags_summary = result.get("tags", {})
    return llm, meta, tags_summary


# ---------------------------------------------------------------------------
# 1. CSV Sidecar
# ---------------------------------------------------------------------------

# Ordered column list — matches common DISCO / sync-CMS import templates
_CSV_FIELDS = [
    "Filename",
    "Title",
    "Artist",
    "Album",
    "Genre",
    "Mood",
    "Instruments",
    "Tempo",
    "Energy",
    "Vocal",
    "Track_Type",
    "BPM",
    "ISRC",
    "Composer",
    "Year",
    "Duration",
    "Comments",
    "Pitch",
]


def write_csv_sidecar(result: dict, csv_path: Union[str, Path]) -> Path:
    """
    Write a single-row CSV sidecar for *result*.

    Parameters
    ----------
    result   : dict returned by SyncTagger.run()
    csv_path : destination file path (.csv)

    Returns
    -------
    Resolved Path of the written file.
    """
    csv_path = Path(csv_path)
    llm, meta, tags_summary = _meta_and_tags(result)
    pitch = llm.get("pitch", "")
    audio_info = result.get("audio_info", {})

    # Genre: LLM may return a scalar; fallback to top zero-shot pick
    genre_raw = meta.get("Genre", tags_summary.get("genre", [""])[:1])
    genre_val = genre_raw[0] if isinstance(genre_raw, list) else _str(genre_raw)

    row = {
        "Filename":    Path(result["file"]).name,
        "Title":       _str(meta.get("Title") or result.get("title", "")),
        "Artist":      _str(meta.get("Artist", "")),
        "Album":       _str(meta.get("Album", "")),
        "Genre":       genre_val,
        "Mood":        _join(meta.get("Mood", tags_summary.get("mood", []))),
        "Instruments": _join(meta.get("Instruments", tags_summary.get("instruments", []))),
        "Tempo":       _str(meta.get("Tempo") or tags_summary.get("tempo", "")),
        "Energy":      _str(meta.get("Energy", "")),
        "Vocal":       _str(meta.get("Vocal", "")),
        "Track_Type":  _str(meta.get("Track_Type", "")),
        "BPM":         _str(meta.get("BPM") or ""),
        "ISRC":        _str(meta.get("ISRC") or ""),
        "Composer":    _str(meta.get("Composer") or ""),
        "Year":        _str(meta.get("Year") or ""),
        "Duration":    _str(audio_info.get("duration", "")),
        "Comments":    _str(meta.get("Comments", "")),
        "Pitch":       pitch,
    }

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerow(row)

    return csv_path.resolve()


# ---------------------------------------------------------------------------
# 2. ID3v2 Tag Embedding
# ---------------------------------------------------------------------------

def write_id3_tags(audio_path: Union[str, Path], result: dict) -> Path:
    """
    Embed metadata from *result* into *audio_path* in-place.

    For ID3-capable containers (MP3, WAV, AIFF, AAC) native ID3v2 frames are
    written.  For FLAC, equivalent Vorbis Comment keys are used.

    Parameters
    ----------
    audio_path : path to the audio file to tag (modified in-place)
    result     : dict returned by SyncTagger.run()

    Returns
    -------
    Resolved Path of the tagged file.
    """
    try:
        import mutagen
    except ImportError:
        raise ImportError(
            "mutagen is required for ID3 embedding. "
            "Install with: pip install mutagen"
        )

    audio_path = Path(audio_path).resolve()
    llm, meta, tags_summary = _meta_and_tags(result)
    pitch = llm.get("pitch", "")

    suffix = audio_path.suffix.lower()
    if suffix == ".flac":
        _tag_vorbis(audio_path, meta, tags_summary, pitch, result)
    else:
        # MP3, WAV (ID3 chunk), AIFF, AAC, M4A and most other containers
        _tag_id3(audio_path, meta, tags_summary, pitch, result)

    return audio_path


# ---- ID3v2 (MP3 / WAV / AIFF) ------------------------------------------ #

def _tag_id3(
    audio_path: Path,
    meta: dict,
    tags_summary: dict,
    pitch: str,
    result: dict,
) -> None:
    from mutagen.id3 import (
        ID3, ID3NoHeaderError,
        TIT2, TPE1, TALB, TCON, TBPM, TSRC, TCOM, TDRC,
        COMM, TIT1, TXXX,
    )

    try:
        tags = ID3(str(audio_path))
    except ID3NoHeaderError:
        # File has no ID3 block yet (common for raw WAV/AIFF)
        tags = ID3()

    # ── Standard frames ──────────────────────────────────────────────── #
    title = meta.get("Title") or result.get("title", "")
    tags["TIT2"] = TIT2(encoding=3, text=[_str(title)])
    tags["TPE1"] = TPE1(encoding=3, text=[_str(meta.get("Artist", ""))])
    tags["TALB"] = TALB(encoding=3, text=[_str(meta.get("Album", ""))])

    # Genre — ID3 TCON accepts plain string (no numeric ID needed)
    genre_raw = meta.get("Genre", tags_summary.get("genre", [""])[:1])
    genre_val = genre_raw[0] if isinstance(genre_raw, list) else _str(genre_raw)
    tags["TCON"] = TCON(encoding=3, text=[genre_val])

    if meta.get("BPM"):
        tags["TBPM"] = TBPM(encoding=3, text=[_str(meta["BPM"])])
    if meta.get("ISRC"):
        tags["TSRC"] = TSRC(encoding=3, text=[_str(meta["ISRC"])])
    if meta.get("Composer"):
        tags["TCOM"] = TCOM(encoding=3, text=[_str(meta["Composer"])])
    if meta.get("Year"):
        tags["TDRC"] = TDRC(encoding=3, text=[_str(meta["Year"])])

    # ── Comments — pitch + licensing notes ───────────────────────────── #
    comments_text = _str(meta.get("Comments", ""))
    if pitch:
        comments_text = f"{pitch}\n\n{comments_text}".strip()
    # COMM key format: "COMM:desc:lang"
    tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=[comments_text])

    # ── TIT1 (Grouping / Content Group) → top moods ──────────────────── #
    mood_raw = meta.get("Mood", tags_summary.get("mood", []))
    mood_str = _join(mood_raw)
    tags["TIT1"] = TIT1(encoding=3, text=[mood_str])

    # ── Custom TXXX frames — survive round-trips through most DAWs/CMS ─ #
    instr_raw = meta.get("Instruments", tags_summary.get("instruments", []))
    tags["TXXX:Mood"]       = TXXX(encoding=3, desc="Mood",       text=[mood_str])
    tags["TXXX:Instruments"]= TXXX(encoding=3, desc="Instruments",text=[_join(instr_raw)])
    tags["TXXX:Tempo"]      = TXXX(encoding=3, desc="Tempo",
                                   text=[_str(meta.get("Tempo") or tags_summary.get("tempo", ""))])
    tags["TXXX:Energy"]     = TXXX(encoding=3, desc="Energy",     text=[_str(meta.get("Energy", ""))])
    tags["TXXX:Vocal"]      = TXXX(encoding=3, desc="Vocal",      text=[_str(meta.get("Vocal", ""))])
    tags["TXXX:Track_Type"] = TXXX(encoding=3, desc="Track_Type", text=[_str(meta.get("Track_Type", ""))])
    if pitch:
        tags["TXXX:Pitch"]  = TXXX(encoding=3, desc="Pitch",      text=[pitch])

    tags.save(str(audio_path))


# ---- Vorbis Comment (FLAC) --------------------------------------- #

def _tag_vorbis(
    audio_path: Path,
    meta: dict,
    tags_summary: dict,
    pitch: str,
    result: dict,
) -> None:
    import mutagen

    f = mutagen.File(str(audio_path))
    if f is None:
        raise ValueError(f"Cannot open audio file for tagging: {audio_path}")
    if f.tags is None:
        f.add_tags()

    title = meta.get("Title") or result.get("title", "")
    genre_raw = meta.get("Genre", tags_summary.get("genre", [""])[:1])
    genre_val = genre_raw[0] if isinstance(genre_raw, list) else _str(genre_raw)

    mood_raw = meta.get("Mood", tags_summary.get("mood", []))
    instr_raw = meta.get("Instruments", tags_summary.get("instruments", []))

    comments_text = _str(meta.get("Comments", ""))
    if pitch:
        comments_text = f"{pitch}\n\n{comments_text}".strip()

    vc = f.tags
    vc["TITLE"]       = [_str(title)]
    vc["ARTIST"]      = [_str(meta.get("Artist", ""))]
    vc["ALBUM"]       = [_str(meta.get("Album", ""))]
    vc["GENRE"]       = [genre_val]
    vc["COMMENT"]     = [comments_text]
    vc["MOOD"]        = [_join(mood_raw)]
    vc["INSTRUMENTS"] = [_join(instr_raw)]
    vc["TEMPO"]       = [_str(meta.get("Tempo") or tags_summary.get("tempo", ""))]
    vc["ENERGY"]      = [_str(meta.get("Energy", ""))]
    vc["VOCAL"]       = [_str(meta.get("Vocal", ""))]
    vc["TRACK_TYPE"]  = [_str(meta.get("Track_Type", ""))]
    if meta.get("BPM"):
        vc["BPM"] = [_str(meta["BPM"])]
    if meta.get("ISRC"):
        vc["ISRC"] = [_str(meta["ISRC"])]
    if meta.get("Composer"):
        vc["COMPOSER"] = [_str(meta["Composer"])]
    if meta.get("Year"):
        vc["DATE"] = [_str(meta["Year"])]
    if pitch:
        vc["PITCH"] = [pitch]

    f.save()
