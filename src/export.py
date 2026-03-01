"""
SyncTag AI — Output Exporters

Two industry-standard output formats produced after every pipeline run:

  1. CSV Sidecar  — single-row file mapping filename → all tags.
                   Used by sync agencies for bulk CMS import.

  2. Tagged Audio — metadata embedded via FFmpeg (-codec:a copy).
                   Audio is bit-for-bit identical to the upload.
                   Metadata is written in the standard chunk for each format:
                     WAV   → RIFF LIST/INFO chunk  (visible in OS + DAWs)
                     MP3   → ID3v2.3               (universal)
                     FLAC  → Vorbis Comments       (universal)
                     AIFF  → ID3v2                 (iTunes / Logic)
                     AAC   → iTunes/MP4 atoms      (universal)
"""

import csv
import subprocess
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


def _meta_and_tags(result: dict) -> tuple[dict, dict]:
    """Unpack the metadata and tags sub-dicts used by both exporters."""
    meta = result.get("metadata", {})
    tags_summary = result.get("tags", {})
    return meta, tags_summary


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
    meta, tags_summary = _meta_and_tags(result)
    audio_info = result.get("audio_info", {})

    # Genre may be a scalar or list; fallback to top zero-shot pick
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
    }

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerow(row)

    return csv_path.resolve()


# ---------------------------------------------------------------------------
# 2. FFmpeg Tag Embedding  (replaces the old mutagen-based write_id3_tags)
# ---------------------------------------------------------------------------

def write_tags_ffmpeg(
    src_path: Union[str, Path],
    dst_path: Union[str, Path],
    result: dict,
) -> Path:
    """
    Copy *src_path* to *dst_path* with metadata embedded via FFmpeg.

    Audio is copied without re-encoding (-codec:a copy), so the output is
    bit-for-bit identical to the input at the audio level.  FFmpeg writes
    metadata in the standard container for each format:
      WAV   → RIFF LIST/INFO chunk  (visible in OS, DAWs, sync CMS)
      MP3   → ID3v2.3
      FLAC  → Vorbis Comments
      AIFF  → ID3 chunk
      AAC   → iTunes/MP4 atoms

    Parameters
    ----------
    src_path : original uploaded audio file
    dst_path : destination for the tagged copy (may be a different filename)
    result   : dict returned by SyncTagger.run()

    Returns
    -------
    Resolved Path of the tagged output file.

    Raises
    ------
    RuntimeError if ffmpeg exits with a non-zero status.
    """
    src_path = Path(src_path).resolve()
    dst_path = Path(dst_path).resolve()
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    meta, tags_summary = _meta_and_tags(result)

    title = _str(meta.get("Title") or result.get("title", ""))
    artist = _str(meta.get("Artist", ""))
    album = _str(meta.get("Album", ""))

    genre_raw = meta.get("Genre", tags_summary.get("genre", [""])[:1])
    genre_val = genre_raw[0] if isinstance(genre_raw, list) else _str(genre_raw)

    comment = _str(meta.get("Comments", ""))
    mood_str = _join(meta.get("Mood", tags_summary.get("mood", [])))
    instr_str = _join(meta.get("Instruments", tags_summary.get("instruments", [])))
    tempo_str = _str(meta.get("Tempo") or tags_summary.get("tempo", ""))
    energy_str = _str(meta.get("Energy", ""))
    vocal_str = _str(meta.get("Vocal", ""))
    track_type_str = _str(meta.get("Track_Type", ""))

    # Build -metadata flags.  FFmpeg maps these to the correct container fields.
    metadata_args: list[str] = []
    for key, val in [
        ("title",        title),
        ("artist",       artist),
        ("album",        album),
        ("genre",        genre_val),
        ("comment",      comment),
        ("mood",         mood_str),
        ("instruments",  instr_str),
        ("tempo",        tempo_str),
        ("energy",       energy_str),
        ("vocal",        vocal_str),
        ("track_type",   track_type_str),
    ]:
        if val:
            metadata_args += ["-metadata", f"{key}={val}"]

    if meta.get("BPM"):
        metadata_args += ["-metadata", f"bpm={_str(meta['BPM'])}"]
    if meta.get("ISRC"):
        metadata_args += ["-metadata", f"isrc={_str(meta['ISRC'])}"]
    if meta.get("Composer"):
        metadata_args += ["-metadata", f"composer={_str(meta['Composer'])}"]
    if meta.get("Year"):
        metadata_args += ["-metadata", f"date={_str(meta['Year'])}"]

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src_path),
        "-codec:a", "copy",   # no re-encoding — audio is bit-for-bit identical
        *metadata_args,
        str(dst_path),
    ]

    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg metadata embedding failed:\n{proc.stderr.decode(errors='replace')}"
        )

    return dst_path.resolve()


# ---------------------------------------------------------------------------
# 2b. mutagen ID3v2 Tag Embedding  (kept for CLI / non-API use)
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
    meta, tags_summary = _meta_and_tags(result)

    suffix = audio_path.suffix.lower()
    if suffix == ".flac":
        _tag_vorbis(audio_path, meta, tags_summary, result)
    elif suffix in (".wav",):
        _tag_wav(audio_path, meta, tags_summary, result)
    elif suffix in (".aif", ".aiff"):
        _tag_aiff(audio_path, meta, tags_summary, result)
    else:
        # MP3, AAC, M4A and other ID3-capable containers
        _tag_id3(audio_path, meta, tags_summary, result)

    return audio_path


# ---- Shared ID3 frame writer (used by WAV, AIFF, and raw ID3 paths) ------ #

def _fill_id3_tags(tags, meta: dict, tags_summary: dict, result: dict) -> None:
    """Populate an ID3 tag object (mutagen.id3.ID3 or compatible) with SyncTag metadata."""
    from mutagen.id3 import (
        TIT2, TPE1, TALB, TCON, TBPM, TSRC, TCOM, TDRC,
        COMM, TIT1, TXXX,
    )

    title = meta.get("Title") or result.get("title", "")
    tags["TIT2"] = TIT2(encoding=3, text=[_str(title)])
    tags["TPE1"] = TPE1(encoding=3, text=[_str(meta.get("Artist", ""))])
    tags["TALB"] = TALB(encoding=3, text=[_str(meta.get("Album", ""))])

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

    comments_text = _str(meta.get("Comments", ""))
    tags["COMM::eng"] = COMM(encoding=3, lang="eng", desc="", text=[comments_text])

    mood_raw = meta.get("Mood", tags_summary.get("mood", []))
    mood_str = _join(mood_raw)
    tags["TIT1"] = TIT1(encoding=3, text=[mood_str])

    instr_raw = meta.get("Instruments", tags_summary.get("instruments", []))
    tags["TXXX:Mood"]        = TXXX(encoding=3, desc="Mood",        text=[mood_str])
    tags["TXXX:Instruments"] = TXXX(encoding=3, desc="Instruments", text=[_join(instr_raw)])
    tags["TXXX:Tempo"]       = TXXX(encoding=3, desc="Tempo",
                                    text=[_str(meta.get("Tempo") or tags_summary.get("tempo", ""))])
    tags["TXXX:Energy"]      = TXXX(encoding=3, desc="Energy",      text=[_str(meta.get("Energy", ""))])
    tags["TXXX:Vocal"]       = TXXX(encoding=3, desc="Vocal",       text=[_str(meta.get("Vocal", ""))])
    tags["TXXX:Track_Type"]  = TXXX(encoding=3, desc="Track_Type",  text=[_str(meta.get("Track_Type", ""))])


# ---- WAV (RIFF id3  chunk) ----------------------------------------------- #

def _tag_wav(
    audio_path: Path,
    meta: dict,
    tags_summary: dict,
    result: dict,
) -> None:
    """Embed ID3 tags into a WAV file using mutagen.wave.WAVE.

    WAVE wraps the standard ID3 API but saves the tags into the RIFF ``id3 ``
    chunk (lowercase, 4-byte FourCC) rather than prepending a raw ID3 block.
    This keeps the WAV spec-compliant and playable in browsers and DAWs.
    """
    from mutagen.wave import WAVE

    audio = WAVE(str(audio_path))
    if audio.tags is None:
        audio.add_tags()
    _fill_id3_tags(audio.tags, meta, tags_summary, result)
    audio.save()


# ---- AIFF (RIFF ID3  chunk) ---------------------------------------------- #

def _tag_aiff(
    audio_path: Path,
    meta: dict,
    tags_summary: dict,
    result: dict,
) -> None:
    """Embed ID3 tags into an AIFF file using mutagen.aiff.AIFF."""
    from mutagen.aiff import AIFF

    audio = AIFF(str(audio_path))
    if audio.tags is None:
        audio.add_tags()
    _fill_id3_tags(audio.tags, meta, tags_summary, result)
    audio.save()


# ---- ID3v2 (MP3 / AAC / M4A and other containers) ----------------------- #

def _tag_id3(
    audio_path: Path,
    meta: dict,
    tags_summary: dict,
    result: dict,
) -> None:
    """Embed ID3 tags into an MP3 or other ID3-native container."""
    from mutagen.id3 import ID3, ID3NoHeaderError

    try:
        tags = ID3(str(audio_path))
    except ID3NoHeaderError:
        tags = ID3()

    _fill_id3_tags(tags, meta, tags_summary, result)
    tags.save(str(audio_path))


# ---- Vorbis Comment (FLAC) --------------------------------------- #

def _tag_vorbis(
    audio_path: Path,
    meta: dict,
    tags_summary: dict,
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

    vc = f.tags
    vc["TITLE"]       = [_str(title)]
    vc["ARTIST"]      = [_str(meta.get("Artist", ""))]
    vc["ALBUM"]       = [_str(meta.get("Album", ""))]
    vc["GENRE"]       = [genre_val]
    vc["COMMENT"]     = [_str(meta.get("Comments", ""))]
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

    f.save()
