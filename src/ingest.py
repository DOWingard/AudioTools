import argparse
import logging
import os
from pathlib import Path

from tqdm import tqdm

from src.embedder import AudioEmbedder
from src.database import AudioDatabase

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".aiff", ".m4a"}


def run_ingest(samples_dir: str, qdrant_host: str) -> None:
    embedder = AudioEmbedder()
    db = AudioDatabase(host=qdrant_host)

    paths = [
        p.resolve()
        for p in Path(samples_dir).rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    logger.info("Found %d audio files in '%s'.", len(paths), samples_dir)

    for path in tqdm(paths, desc="Ingesting", unit="file"):
        try:
            waveform = embedder.load_audio(str(path))
            emb = embedder.embed(waveform)
            db.upsert(emb, str(path))
        except Exception as e:
            logger.error("Failed %s: %s", path, e, exc_info=True)
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest audio files into Qdrant.")
    parser.add_argument(
        "--samples-dir",
        default="samples/",
        help="Directory containing audio samples (default: samples/)",
    )
    parser.add_argument(
        "--qdrant-host",
        default=os.environ.get("QDRANT_HOST", "localhost"),
        help="Qdrant host (default: QDRANT_HOST env or localhost)",
    )
    args = parser.parse_args()
    run_ingest(args.samples_dir, args.qdrant_host)


if __name__ == "__main__":
    main()
