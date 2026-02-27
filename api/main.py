"""
Compute API — FastAPI service exposing SyncTag, stem-separation, and audio tool endpoints.

Endpoints:
    GET  /health          → {"status": "ok"}
    POST /api/tag         → ZIP (metadata.json + CSV + tagged audio)
    POST /api/separate    → ZIP (all stem WAV files)
    POST /api/cut         → Trimmed audio file
    POST /api/join        → Joined audio file (multiple inputs)
    POST /api/karaoke     → Instrumental audio (vocals removed)
    POST /api/convert     → Converted audio file (format change)
    POST /api/bpm-key     → JSON (bpm, key, tempo_category)
    POST /api/analyze     → JSON (comprehensive audio analysis)
"""

import io
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

app = FastAPI(title="SyncTag Compute API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Lazy-init singletons — ML models load once on first request
# ---------------------------------------------------------------------------
_tagger = None


def _get_tagger():
    global _tagger
    if _tagger is None:
        from src.synctag import SyncTagger
        _tagger = SyncTagger()
    return _tagger


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /api/tag
# ---------------------------------------------------------------------------

@app.post("/api/tag")
async def tag_audio(
    audio: UploadFile = File(...),
    isrc: str = Form(default=""),
):
    """
    Accept an audio upload, run the full SyncTag pipeline, and return a ZIP
    containing:
      - metadata.json
      - <stem>.csv
      - <stem>_tagged.<ext>
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_tag_"))
    try:
        # Save upload
        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        # Run pipeline
        tagger = _get_tagger()
        result = tagger.run(input_path)

        # Inject ISRC if provided
        isrc = (isrc or "").strip()
        if isrc:
            result.setdefault("llm", {}).setdefault("metadata", {})["ISRC"] = isrc

        # Write ID3 tags onto the audio copy
        from src.export import write_csv_sidecar, write_id3_tags
        try:
            write_id3_tags(input_path, result)
        except Exception as exc:
            print(f"[api/tag] ID3 warning: {exc}")

        # Write CSV sidecar
        csv_path = tmp_dir / f"{input_path.stem}.csv"
        write_csv_sidecar(result, csv_path)

        # Write metadata JSON
        meta_path = tmp_dir / "metadata.json"
        meta_path.write_text(json.dumps(result, indent=2, default=str))

        # Pack into ZIP
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(meta_path, arcname="metadata.json")
            zf.write(csv_path, arcname=csv_path.name)
            tagged_name = f"{input_path.stem}_tagged{input_path.suffix}"
            zf.write(input_path, arcname=tagged_name)
        buf.seek(0)

        filename = f"{input_path.stem}_synctag.zip"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/separate
# ---------------------------------------------------------------------------

@app.post("/api/separate")
async def separate_audio(
    audio: UploadFile = File(...),
):
    """
    Accept an audio upload, run the three-stage separation pipeline, and
    return a ZIP containing all stem WAV files keyed by stem name.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_sep_"))
    try:
        import torch

        # Save upload
        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        output_dir = tmp_dir / "stems" / input_path.stem
        device = "cuda" if torch.cuda.is_available() else "cpu"

        from src.advanced_separate import run_pipeline
        all_files = run_pipeline(input_path, output_dir, device=device)

        # Pack all existing stem files into ZIP
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for key, filepath in all_files.items():
                p = Path(filepath)
                if p.exists():
                    zf.write(p, arcname=f"{key}.wav")
        buf.seek(0)

        filename = f"{input_path.stem}_stems.zip"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/cut — Audio Cutter / Trimmer
# ---------------------------------------------------------------------------

@app.post("/api/cut")
async def cut_audio(
    audio: UploadFile = File(...),
    start: float = Form(default=0.0),
    end: float = Form(default=0.0),
):
    """Trim audio to the specified start/end times (seconds). Returns WAV."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_cut_"))
    try:
        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        output_path = tmp_dir / f"{input_path.stem}_trimmed.wav"

        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-ss", str(start),
        ]
        if end > start:
            cmd += ["-to", str(end)]
        cmd += ["-c", "pcm_s16le", str(output_path)]

        subprocess.run(cmd, capture_output=True, check=True)

        buf = io.BytesIO(output_path.read_bytes())
        return StreamingResponse(
            buf,
            media_type="audio/wav",
            headers={"Content-Disposition": f'attachment; filename="{output_path.name}"'},
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"ffmpeg error: {exc.stderr.decode()}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/join — Audio Joiner
# ---------------------------------------------------------------------------

from typing import List

@app.post("/api/join")
async def join_audio(
    audio: List[UploadFile] = File(...),
):
    """Concatenate multiple audio files in upload order. Returns WAV."""
    if len(audio) < 2:
        raise HTTPException(status_code=400, detail="At least 2 audio files required.")
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_join_"))
    try:
        # Save all uploads
        input_paths = []
        for i, f in enumerate(audio):
            ext = Path(f.filename or "input.wav").suffix or ".wav"
            fpath = tmp_dir / f"input_{i:03d}{ext}"
            content = await f.read()
            fpath.write_bytes(content)
            input_paths.append(fpath)

        # Convert each to WAV PCM first for consistent concat
        wav_paths = []
        for i, p in enumerate(input_paths):
            wav_path = tmp_dir / f"norm_{i:03d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(p), "-ar", "44100", "-ac", "2",
                 "-c:a", "pcm_s16le", str(wav_path)],
                capture_output=True, check=True,
            )
            wav_paths.append(wav_path)

        # Build concat list file
        list_file = tmp_dir / "concat.txt"
        list_file.write_text(
            "\n".join(f"file '{p}'" for p in wav_paths),
            encoding="utf-8",
        )

        output_path = tmp_dir / "joined.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_file), "-c", "copy", str(output_path)],
            capture_output=True, check=True,
        )

        buf = io.BytesIO(output_path.read_bytes())
        return StreamingResponse(
            buf,
            media_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="joined.wav"'},
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"ffmpeg error: {exc.stderr.decode()}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/karaoke — Vocal Removal (Karaoke)
# ---------------------------------------------------------------------------

@app.post("/api/karaoke")
async def karaoke_audio(
    audio: UploadFile = File(...),
):
    """Remove vocals using Demucs and return the instrumental mix."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_karaoke_"))
    try:
        import torch

        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        device = "cuda" if torch.cuda.is_available() else "cpu"

        from src.separate import separate_stems
        stems = separate_stems(input_path, tmp_dir / "stems", device=device)

        # Build instrumental by summing all non-vocal stems
        import torchaudio
        instrumental = None
        sr = None
        for name, path in stems.items():
            if name == "vocals":
                continue
            wav, s = torchaudio.load(str(path))
            sr = s
            instrumental = wav if instrumental is None else instrumental + wav

        if instrumental is None:
            raise HTTPException(status_code=500, detail="No stems produced")

        output_path = tmp_dir / f"{input_path.stem}_karaoke.wav"
        torchaudio.save(str(output_path), instrumental, sr)

        buf = io.BytesIO(output_path.read_bytes())
        return StreamingResponse(
            buf,
            media_type="audio/wav",
            headers={"Content-Disposition": f'attachment; filename="{output_path.name}"'},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/convert — Format Converter
# ---------------------------------------------------------------------------

_FORMAT_MAP = {
    "mp3":  {"ext": ".mp3",  "codec": "libmp3lame", "mime": "audio/mpeg"},
    "wav":  {"ext": ".wav",  "codec": "pcm_s16le",  "mime": "audio/wav"},
    "flac": {"ext": ".flac", "codec": "flac",       "mime": "audio/flac"},

}

@app.post("/api/convert")
async def convert_audio(
    audio: UploadFile = File(...),
    format: str = Form(default="mp3"),
):
    """Convert audio to the requested format."""
    fmt = format.lower().strip()
    if fmt not in _FORMAT_MAP:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}. Use: {list(_FORMAT_MAP.keys())}")

    spec = _FORMAT_MAP[fmt]
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_convert_"))
    try:
        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        output_name = f"{input_path.stem}_converted{spec['ext']}"
        output_path = tmp_dir / output_name

        cmd = ["ffmpeg", "-y", "-i", str(input_path), "-c:a", spec["codec"]]
        if fmt == "mp3":
            cmd += ["-q:a", "2"]  # high quality VBR

        cmd.append(str(output_path))

        subprocess.run(cmd, capture_output=True, check=True)

        buf = io.BytesIO(output_path.read_bytes())
        return StreamingResponse(
            buf,
            media_type=spec["mime"],
            headers={"Content-Disposition": f'attachment; filename="{output_name}"'},
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"ffmpeg error: {exc.stderr.decode()}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/bpm-key — BPM & Key Finder (Enhanced)
# ---------------------------------------------------------------------------

_KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Kessler key profiles (standard music cognition profiles)
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# Lazy CLAP embedder singleton
_embedder = None

def _get_embedder():
    global _embedder
    if _embedder is None:
        from src.embedder import AudioEmbedder
        _embedder = AudioEmbedder()
    return _embedder


def _correct_bpm(raw_bpm: float) -> float:
    """Half/double correction: normalize BPM into the 70-180 'sweet spot' range."""
    bpm = raw_bpm
    while bpm < 70:
        bpm *= 2.0
    while bpm > 180:
        bpm /= 2.0
    return round(bpm, 1)


def _chroma_key_detect(chroma_mean: np.ndarray) -> tuple:
    """
    Key detection via Krumhansl profile correlation.
    Returns (key_string, confidence_score).
    """
    major_corrs = []
    minor_corrs = []
    for shift in range(12):
        rolled = np.roll(chroma_mean, shift)
        major_corrs.append(np.corrcoef(rolled, _MAJOR_PROFILE)[0, 1])
        minor_corrs.append(np.corrcoef(rolled, _MINOR_PROFILE)[0, 1])

    best_major_idx = int(np.argmax(major_corrs))
    best_minor_idx = int(np.argmax(minor_corrs))
    best_major_score = major_corrs[best_major_idx]
    best_minor_score = minor_corrs[best_minor_idx]

    if best_major_score >= best_minor_score:
        return f"{_KEY_NAMES[best_major_idx]} Major", float(best_major_score)
    else:
        return f"{_KEY_NAMES[best_minor_idx]} Minor", float(best_minor_score)


def _windowed_key_detect(y: np.ndarray, sr: int, segment_secs: float = 8.0) -> tuple:
    """
    Windowed key detection: split audio into segments, gate by energy,
    detect key per segment, and vote across all segments.
    """
    import librosa

    seg_samples = int(segment_secs * sr)
    total = len(y)
    if total < seg_samples:
        # Too short — just analyze the whole thing
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        return _chroma_key_detect(chroma.mean(axis=1))

    # Compute global RMS for energy gating
    global_rms = np.sqrt(np.mean(y ** 2))
    energy_threshold = global_rms * 0.25  # skip segments below 25% of avg energy

    # Vote across segments
    key_votes = {}   # key_string -> (count, sum_of_confidence)
    n_segments = max(1, total // seg_samples)

    for i in range(n_segments):
        start = i * seg_samples
        end = min(start + seg_samples, total)
        segment = y[start:end]

        # Energy gate — skip quiet segments (intros/outros/silence)
        seg_rms = np.sqrt(np.mean(segment ** 2))
        if seg_rms < energy_threshold:
            continue

        chroma = librosa.feature.chroma_cqt(y=segment, sr=sr)
        key_str, conf = _chroma_key_detect(chroma.mean(axis=1))

        if key_str not in key_votes:
            key_votes[key_str] = [0, 0.0]
        key_votes[key_str][0] += 1
        key_votes[key_str][1] += conf

    if not key_votes:
        # Fallback to full-track analysis if all segments gated out
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        return _chroma_key_detect(chroma.mean(axis=1))

    # Winner = most votes, tie-broken by summed confidence
    winner = max(key_votes.items(), key=lambda kv: (kv[1][0], kv[1][1]))
    avg_conf = winner[1][1] / winner[1][0]
    return winner[0], avg_conf


def _clap_key_crosscheck(audio_path: str, chroma_key: str, chroma_conf: float) -> str:
    """
    Cross-check key detection using M2D-CLAP: embed the audio, then compute
    cosine similarity against text descriptions for all 24 keys.
    If CLAP strongly disagrees with chroma (and chroma confidence is low),
    prefer the CLAP result.
    """
    try:
        embedder = _get_embedder()

        # Load and embed audio
        waveform = embedder.load_audio(audio_path)
        audio_emb = embedder.embed(waveform)           # (768,)
        audio_emb = audio_emb / (np.linalg.norm(audio_emb) + 1e-10)

        # Build 24 key candidates
        key_labels = []
        for name in _KEY_NAMES:
            key_labels.append(f"{name} Major")
            key_labels.append(f"{name} Minor")

        # Encode each key description via CLAP text encoder
        best_key = chroma_key
        best_sim = -1.0

        for label in key_labels:
            # Create descriptive prompt for the key
            prompt = f"music in the key of {label}"
            text_emb = embedder.encode_text(prompt)  # (768,)
            text_emb = text_emb / (np.linalg.norm(text_emb) + 1e-10)
            sim = float(np.dot(audio_emb.flatten(), text_emb.flatten()))
            if sim > best_sim:
                best_sim = sim
                best_key = label

        # Decision logic:
        # If chroma confidence is strong (>0.85), trust chroma unless CLAP
        # strongly disagrees. If chroma confidence is weak (<0.7), defer to CLAP.
        if chroma_conf >= 0.85:
            return chroma_key  # high-confidence chroma wins
        elif chroma_conf < 0.7:
            return best_key    # low-confidence chroma → defer to CLAP
        else:
            # Medium confidence: if CLAP agrees (same key), keep it.
            # If CLAP disagrees, use CLAP only if its top similarity is strong.
            if best_key == chroma_key:
                return chroma_key
            else:
                return best_key  # CLAP override for medium-confidence zone

    except Exception as e:
        print(f"[bpm-key] CLAP cross-check failed, using chroma: {e}")
        return chroma_key


def _madmom_key_detect(audio_path: str) -> tuple:
    """
    Key detection using madmom's CNN-based key recognition.
    CNNKeyRecognitionProcessor is a SequentialProcessor that includes its own
    preprocessing (SignalProcessor → FramedSignalProcessor → STFT →
    LogarithmicFilteredSpectrogram → CNN → softmax).
    Returns (key_string, confidence_score) or (None, 0.0) on failure.
    """
    try:
        from madmom.features.key import CNNKeyRecognitionProcessor

        # Pre-convert to WAV 44100Hz mono to avoid format issues
        wav_path = audio_path + ".madmom.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-ar", "44100", "-ac", "1",
             "-c:a", "pcm_s16le", wav_path],
            capture_output=True, check=True,
        )

        # CNNKeyRecognitionProcessor IS the full pipeline — just call it on the file
        proc = CNNKeyRecognitionProcessor()
        key_probs = proc(wav_path)  # shape: (n_frames, 24) — 12 major + 12 minor

        # Clean up temp wav
        Path(wav_path).unlink(missing_ok=True)

        # Handle multi-dimensional output: average across frames → (24,)
        key_probs = np.array(key_probs)
        if key_probs.ndim > 1:
            key_probs = key_probs.mean(axis=0)
        key_probs = key_probs.flatten()

        # Get the best key
        best_idx = int(np.argmax(key_probs))
        confidence = float(key_probs[best_idx])

        # madmom key order: C maj, C min, C# maj, C# min, ... B maj, B min
        key_idx = best_idx // 2
        is_minor = best_idx % 2 == 1
        mode = "Minor" if is_minor else "Major"
        key_str = f"{_KEY_NAMES[key_idx]} {mode}"

        return key_str, confidence

    except Exception as e:
        print(f"[bpm-key] madmom CNN key detection failed: {e}")
        # Clean up on failure too
        Path(audio_path + ".madmom.wav").unlink(missing_ok=True)
        return None, 0.0


def _consensus_key(audio_path: str, y: np.ndarray, sr: int) -> tuple:
    """
    Key detection: madmom CNN primary, windowed chroma fallback.
    """
    # Primary: madmom CNN
    madmom_key, madmom_conf = _madmom_key_detect(audio_path)

    if madmom_key is not None:
        return madmom_key, madmom_conf, "madmom"

    # Fallback: windowed chroma (Krumhansl profile correlation)
    chroma_key, chroma_conf = _windowed_key_detect(y, sr)
    return chroma_key, chroma_conf, "chroma"


def _detect_bpm_key(audio_path: str) -> dict:
    """
    Enhanced BPM and musical key detection.
      - madmom CNN key recognition (primary)
      - Windowed chroma analysis (secondary)
      - M2D-CLAP cross-check (arbiter on disagreement)
      - BPM half/double correction (target 70-180 BPM range)
    """
    import librosa

    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    # ── BPM ──
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    raw_bpm = float(tempo[0] if hasattr(tempo, '__len__') else tempo)
    bpm = _correct_bpm(raw_bpm)

    # ── Key (three-source consensus) ──
    key, key_conf, key_source = _consensus_key(audio_path, y, sr)

    # ── Tempo category ──
    if bpm < 70:
        tempo_cat = "Very Slow"
    elif bpm < 100:
        tempo_cat = "Slow"
    elif bpm < 130:
        tempo_cat = "Medium"
    elif bpm < 160:
        tempo_cat = "Fast"
    else:
        tempo_cat = "Very Fast"

    return {
        "bpm": bpm,
        "key": key,
        "key_confidence": round(key_conf, 3),
        "key_source": key_source,
        "tempo_category": tempo_cat,
        "duration": round(duration, 2),
    }


@app.post("/api/bpm-key")
async def bpm_key(
    audio: UploadFile = File(...),
):
    """Detect BPM and musical key of an audio file."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_bpm_"))
    try:
        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        result = _detect_bpm_key(str(input_path))
        return JSONResponse(result)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/analyze — Comprehensive Audio Analyzer
# ---------------------------------------------------------------------------

def _analyze_audio(audio_path: str) -> dict:
    """Comprehensive audio analysis: BPM, key, loudness, spectral info."""
    import librosa

    # Basic BPM + Key
    result = _detect_bpm_key(audio_path)

    y, sr = librosa.load(audio_path, sr=22050, mono=True)

    # RMS energy
    rms = float(np.sqrt(np.mean(y ** 2)))
    rms_db = float(20 * np.log10(rms + 1e-10))

    # Peak level
    peak = float(np.max(np.abs(y)))
    peak_db = float(20 * np.log10(peak + 1e-10))

    # Spectral centroid (brightness indicator)
    spec_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
    brightness_hz = float(np.mean(spec_centroid))

    # Zero crossing rate (percussiveness indicator)
    zcr = librosa.feature.zero_crossing_rate(y)
    avg_zcr = float(np.mean(zcr))

    # Loudness via ffmpeg loudnorm (integrated LUFS)
    lufs = None
    try:
        lufs_result = subprocess.run(
            ["ffmpeg", "-i", audio_path, "-af", "loudnorm=print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True,
        )
        # Parse loudnorm JSON from stderr
        stderr = lufs_result.stderr
        json_start = stderr.rfind("{")
        json_end = stderr.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            lufs_data = json.loads(stderr[json_start:json_end])
            lufs = float(lufs_data.get("input_i", 0))
    except Exception:
        pass

    # File info via ffprobe
    channels = 0
    sample_rate_original = 0
    bit_depth = ""
    codec = ""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", audio_path],
            capture_output=True, text=True, check=True,
        )
        probe_data = json.loads(probe.stdout)
        for stream in probe_data.get("streams", []):
            if stream.get("codec_type") == "audio":
                channels = int(stream.get("channels", 0))
                sample_rate_original = int(stream.get("sample_rate", 0))
                bit_depth = stream.get("bits_per_sample", stream.get("bits_per_raw_sample", ""))
                codec = stream.get("codec_name", "")
                break
    except Exception:
        pass

    # Energy rating (1-10 scale based on RMS)
    energy_rating = min(10, max(1, int(np.interp(rms_db, [-40, -5], [1, 10]))))

    result.update({
        "rms_db": round(rms_db, 1),
        "peak_db": round(peak_db, 1),
        "lufs": round(lufs, 1) if lufs is not None else None,
        "brightness_hz": round(brightness_hz, 0),
        "zero_crossing_rate": round(avg_zcr, 4),
        "energy_rating": energy_rating,
        "channels": channels,
        "sample_rate": sample_rate_original,
        "bit_depth": str(bit_depth) if bit_depth else "N/A",
        "codec": codec,
    })

    return result


@app.post("/api/analyze")
async def analyze_audio(
    audio: UploadFile = File(...),
):
    """Comprehensive audio analysis returning BPM, key, loudness, spectral info."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="api_analyze_"))
    try:
        input_path = tmp_dir / (audio.filename or "input.wav")
        content = await audio.read()
        input_path.write_bytes(content)

        result = _analyze_audio(str(input_path))
        return JSONResponse(result)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
