"""
Gradio UI — thin browser frontend for the SyncTag Compute API.

All heavy ML computation runs in the Compute API (FastAPI, port 8000).
This process only uploads files and renders results.

Run:
    python src/app.py
"""

import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

import gradio as gr
import httpx
from dotenv import load_dotenv

load_dotenv()

COMPUTE_API_URL = os.environ.get("COMPUTE_API_URL", "http://localhost:8000")
_TIMEOUT = httpx.Timeout(connect=30.0, read=900.0, write=300.0, pool=10.0)


# =========================================================================== #
# Tab 1 — SyncTag AI
# =========================================================================== #

def tag_track(audio_file: str, isrc: str, progress=gr.Progress()) -> tuple:
    """
    Gradio handler: upload audio to the Compute API and return outputs.
    """
    if not audio_file:
        return None, None, "**Error:** No audio file provided."

    try:
        progress(0.1, desc="Uploading to compute service…")
        audio_path = Path(audio_file)

        with open(audio_path, "rb") as fh:
            resp = httpx.post(
                f"{COMPUTE_API_URL}/api/tag",
                files={"audio": (audio_path.name, fh, "application/octet-stream")},
                data={"isrc": (isrc or "").strip()},
                timeout=_TIMEOUT,
            )

        if resp.status_code != 200:
            detail = resp.text
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            return None, None, f"**Error ({resp.status_code}):** {detail}"

        progress(0.85, desc="Extracting results…")

        # Extract ZIP to a temp directory
        tmp_dir = Path(tempfile.mkdtemp(prefix="synctag_ui_"))
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(tmp_dir)

        # Locate outputs
        meta_path = tmp_dir / "metadata.json"
        result = json.loads(meta_path.read_text()) if meta_path.exists() else {}

        csv_files = list(tmp_dir.glob("*.csv"))
        csv_path = str(csv_files[0]) if csv_files else None

        audio_files = [
            p for p in tmp_dir.iterdir()
            if p.suffix.lower() in {".wav", ".flac", ".mp3", ".aac"}
        ]
        tagged_audio = str(audio_files[0]) if audio_files else None

        progress(0.95, desc="Building summary…")
        summary = _build_summary(result)

        progress(1.0, desc="Done!")
        return tagged_audio, csv_path, summary

    except Exception as exc:
        return None, None, f"**Error:** {exc}"


def _build_summary(result: dict) -> str:
    """Render a markdown summary card from pipeline results."""
    llm = result.get("llm", {})
    meta = llm.get("metadata", {})
    tags = result.get("tags", {})
    audio_info = result.get("audio_info", {})

    pitch = llm.get("pitch", "")
    comments = meta.get("Comments", "")

    genre = meta.get("Genre") or (tags.get("genre") or [""])[0]
    mood_raw = meta.get("Mood") or tags.get("mood") or []
    mood = "; ".join(mood_raw) if isinstance(mood_raw, list) else str(mood_raw)
    tempo = meta.get("Tempo") or tags.get("tempo", "")
    energy = meta.get("Energy", "")
    vocal = meta.get("Vocal", "")
    track_type = meta.get("Track_Type", "")
    isrc_val = meta.get("ISRC", "")
    duration = audio_info.get("duration", "")

    lines = []
    if pitch:
        lines.append(f"## Pitch\n{pitch}\n")

    lines.append("## Metadata")
    lines.append(f"- **Genre:** {genre}")
    lines.append(f"- **Mood:** {mood}")
    lines.append(f"- **Tempo:** {tempo}")
    lines.append(f"- **Energy:** {energy}/10")
    lines.append(f"- **Vocal:** {vocal}")
    lines.append(f"- **Track Type:** {track_type}")
    if duration:
        lines.append(f"- **Duration:** {duration:.1f}s" if isinstance(duration, float) else f"- **Duration:** {duration}s")
    if isrc_val:
        lines.append(f"- **ISRC:** {isrc_val}")

    if comments:
        lines.append(f"\n## Sync Notes\n{comments}")

    return "\n".join(lines)


# =========================================================================== #
# Tab 2 — Stem Separator
# =========================================================================== #

# All stems the pipeline can produce, in display order.
# (internal_key, display_label, group)
_STEM_LAYOUT = [
    # Main Demucs stems
    ("vocals",        "🎤 Vocals",       "main"),
    ("drums",         "🥁 Drums",        "main"),
    ("sub",           "🔊 Sub Bass",     "main"),
    ("midbass",       "🎸 Mid/Other",    "main"),
    # LARS drum kit
    ("drums_kick",    "👟 Kick",         "drums"),
    ("drums_snare",   "🪘 Snare",        "drums"),
    ("drums_toms",    "🔔 Toms",         "drums"),
    ("drums_hihat",   "🎩 Hi-Hat",       "drums"),
    ("drums_cymbals", "💿 Cymbals",      "drums"),
    # One-shots
    ("oneshot_kick",    "👟 Kick Shot",   "oneshots"),
    ("oneshot_snare",   "🪘 Snare Shot",  "oneshots"),
    ("oneshot_toms",    "🔔 Toms Shot",   "oneshots"),
    ("oneshot_hihat",   "🎩 Hi-Hat Shot", "oneshots"),
    ("oneshot_cymbals", "💿 Cymbals Shot","oneshots"),
]


def separate_audio(audio_file: str, progress=gr.Progress()):
    """
    Upload audio to the Compute API separation endpoint and return outputs.
    Returns: (progress_text, zip_file, *stem_audios)
    """
    n_stems = len(_STEM_LAYOUT)
    empty = ("", None) + tuple(None for _ in range(n_stems))

    if not audio_file:
        return ("**Error:** No audio file provided.", None) + tuple(None for _ in range(n_stems))

    try:
        progress(0.05, desc="Uploading to compute service…")
        audio_path = Path(audio_file)

        with open(audio_path, "rb") as fh:
            resp = httpx.post(
                f"{COMPUTE_API_URL}/api/separate",
                files={"audio": (audio_path.name, fh, "application/octet-stream")},
                timeout=_TIMEOUT,
            )

        if resp.status_code != 200:
            detail = resp.text
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            return (f"**Error ({resp.status_code}):** {detail}", None) + tuple(None for _ in range(n_stems))

        progress(0.90, desc="Extracting stems…")

        # Extract ZIP to temp dir
        tmp_dir = Path(tempfile.mkdtemp(prefix="synctag_sep_"))
        zip_bytes = resp.content
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(tmp_dir)

        # Save the ZIP itself for bulk download
        zip_path = tmp_dir / f"{audio_path.stem}_stems.zip"
        zip_path.write_bytes(zip_bytes)

        # Build a dict from extracted stem key → file path
        extracted: dict[str, str] = {}
        for p in tmp_dir.glob("*.wav"):
            key = p.stem  # e.g. "vocals", "drums_kick"
            extracted[key] = str(p)

        # Map results into the fixed stem layout slots
        stem_outputs = []
        for key, _label, _group in _STEM_LAYOUT:
            stem_outputs.append(extracted.get(key))

        n_found = len(extracted)
        progress(1.0, desc="Done!")
        status = f"✅ **Separated {n_found} stems** from `{audio_path.name}`"
        return (status, str(zip_path)) + tuple(stem_outputs)

    except Exception as exc:
        print(f"[app/separate] {type(exc).__name__}: {exc}")
        return (f"**Error:** {exc}", None) + tuple(None for _ in range(n_stems))


# =========================================================================== #
# Gradio UI
# =========================================================================== #

with gr.Blocks(title="Audio Pipeline") as demo:
    gr.Markdown("# 🎛️ Audio Pipeline", elem_classes=["main-title"])
    gr.Markdown("SyncTag AI  •  Stem Separator", elem_classes=["subtitle"])

    with gr.Tabs():
        # ── Tab 1: SyncTag AI ─────────────────────────────────────────── #
        with gr.Tab("🏷️ SyncTag AI", id="synctag"):
            gr.Markdown("Upload audio to auto-tag for sync licensing. Get a tagged copy, CSV sidecar, and metadata summary.")

            with gr.Row():
                with gr.Column(scale=1):
                    st_audio_input = gr.Audio(
                        type="filepath",
                        label="Audio File",
                    )
                    st_isrc_input = gr.Textbox(
                        label="ISRC (optional)",
                        placeholder="e.g. GB-ABC-25-00001",
                        max_lines=1,
                    )
                    st_run_btn = gr.Button("🚀 Tag Track", variant="primary", size="lg")

                with gr.Column(scale=2):
                    st_summary_output = gr.Markdown(label="Summary")
                    with gr.Row():
                        st_audio_output = gr.File(label="Download Tagged Audio")
                        st_csv_output = gr.File(label="Download CSV Sidecar")

            st_run_btn.click(
                fn=tag_track,
                inputs=[st_audio_input, st_isrc_input],
                outputs=[st_audio_output, st_csv_output, st_summary_output],
            )

        # ── Tab 2: Stem Separator ─────────────────────────────────────── #
        with gr.Tab("🎚️ Stem Separator", id="separator"):
            gr.Markdown(
                "Upload a full track to separate into stems. "
                "**Demucs** extracts vocals, drums, bass, & more → "
                "**LARS** splits drums into kick, snare, toms, hihat, & cymbals → "
                "**Gate+Slice** produces one-shot samples from each drum component."
            )

            with gr.Row():
                sep_audio_input = gr.File(
                    label="Audio File (WAV / FLAC / MP3)",
                    file_types=[".wav", ".flac", ".mp3", ".aac"],
                )
                sep_run_btn = gr.Button("🔀 Separate Stems", variant="primary", size="lg")

            # Status + download
            sep_status = gr.Markdown("")
            sep_zip = gr.File(label="📦 Download All Stems (ZIP)", visible=True)

            # ── Pre-defined audio players — vertical stack, full width ──── #
            stem_outputs = []

            def _stem_player(key: str, label: str):
                """Render one audio player + its volume slider, wired via JS."""
                a = gr.Audio(label=label, type="filepath", interactive=False,
                             elem_classes=["stem-player"], elem_id=f"stem_{key}")
                vol = gr.Slider(minimum=0, maximum=1, value=1, step=0.01,
                                label="Volume", elem_classes=["volume-slider"])
                vol.change(
                    fn=None,
                    inputs=[vol],
                    outputs=[],
                    js=(
                        f"(v) => {{"
                        f"  const el = document.querySelector('#stem_{key} audio');"
                        f"  if (el) el.volume = v;"
                        f"  return [];"
                        f"}}"
                    ),
                )
                return a

            gr.Markdown("### 🎵 Main Stems", elem_classes=["group-title"])
            for key, label, group in _STEM_LAYOUT:
                if group != "main":
                    continue
                stem_outputs.append(_stem_player(key, label))

            gr.Markdown("### 🥁 Drum Stems", elem_classes=["group-title"])
            for key, label, group in _STEM_LAYOUT:
                if group != "drums":
                    continue
                stem_outputs.append(_stem_player(key, label))

            gr.Markdown("### 🔊 Drum 1 Shots", elem_classes=["group-title"])
            for key, label, group in _STEM_LAYOUT:
                if group != "oneshots":
                    continue
                stem_outputs.append(_stem_player(key, label))

            def run_separator(file_obj):
                audio_file = file_obj if isinstance(file_obj, str) else (file_obj.name if file_obj else None)
                return separate_audio(audio_file)

            sep_run_btn.click(
                fn=run_separator,
                inputs=[sep_audio_input],
                outputs=[sep_status, sep_zip] + stem_outputs,
            )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        theme=gr.themes.Soft(
            primary_hue="violet",
            secondary_hue="slate",
            neutral_hue="slate",
        ),
        css="""
        .main-title { text-align: center; margin-bottom: 0.2em; }
        .subtitle { text-align: center; color: #8b8b9e; margin-bottom: 1.5em; font-size: 1.05em; }
        .group-title { font-size: 1.3em; font-weight: 700; margin-top: 1.5em; margin-bottom: 0.5em;
                       border-bottom: 2px solid #7c3aed; padding-bottom: 0.3em; }
        /* Full-width vertical stem players — waveform stretches to fit */
        .stem-player { width: 100% !important; max-width: 100% !important; }
        .stem-player audio { width: 100% !important; }
        .stem-player .waveform-container,
        .stem-player .audio-container,
        .stem-player canvas,
        .stem-player svg { width: 100% !important; max-width: 100% !important; }
        /* Volume slider — compact strip directly below each player */
        .volume-slider { margin-top: -0.5em !important; margin-bottom: 0.8em !important; }
        .volume-slider .label-wrap { display: none !important; }
        .volume-slider input[type=range] { height: 4px; }
        """,
    )
