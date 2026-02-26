"""
Compute API — FastAPI service exposing SyncTag and stem-separation endpoints.

Endpoints:
    GET  /health          → {"status": "ok"}
    POST /api/tag         → ZIP (metadata.json + CSV + tagged audio)
    POST /api/separate    → ZIP (all stem WAV files)
"""

import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

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
