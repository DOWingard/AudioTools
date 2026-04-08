# SyncTag AI 🎵

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![React](https://img.shields.io/badge/React-20232A?style=flat&logo=react&logoColor=61DAFB)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=flat&logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)

SyncTag AI is a powerful, full-stack SaaS platform designed for musicians, producers, and sync licensing professionals. It provides state-of-the-art audio tools including metadata generation, stem separation, BPM/key analysis, and audio graph search. The application is built with a highly scalable, multi-container Docker architecture.

## 🚀 Key Features

- **Metadata Sync Tagging**: Zero-shot AI-driven classification to automatically generate rich metadata for sync licensing, including genres, moods, and instruments.
- **Stem Separation**: High-fidelity 4-stem extraction (Vocals, Drums, Bass, Other) using Facebook's Demucs and LARS drum demixing.
- **Karaoke Mode**: Specialized vocal removal for pristine instrumental tracks.
- **Audio Analysis**: Automatic precise BPM detection, Key identification, and format conversion.
- **Visual Audio Graph**: Similarity search utilizing Qdrant vector database and M2D-CLAP embeddings.
- **Audio Workstation Tools**: In-browser precise trimming, joining, and formatting of audio tracks.
- **Enterprise Auth & Billing**: Clerk authentication combined with Stripe embedded checkout, tier-based free limits, and user dashboards.

## 🏗️ Architecture Stack

SyncTag consists of 5 core services deployed via Docker Compose:

1. **`ui`**: Vite/React SPA frontend served via Nginx.
2. **`compute`**: Python FastAPI service running all heavy audio operations (M2D-CLAP, Demucs).
3. **`auth`**: Python FastAPI service handling Clerk JWTs, Stripe webhooks, and the user database.
4. **`qdrant`**: High-performance vector database mapped to audio fingerprinting.
5. **`postgres`**: Relational database for user profiles and subscription event logging.
6. **`minio`**: S3-compatible local/cloud object storage for audio files.

## 📦 Requirements

- Docker & Docker Compose
- Python 3.10+ (for local development outside of Docker)
- Node.js 18+ & npm
- Accounts for **Clerk** (Auth) and **Stripe** (Billing) to provision API keys.

## ⚙️ Quickstart

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-org/synctag-ai.git
   cd synctag-ai
   git submodule update --init --recursive
   ```

2. **Environment Variables:**
   Copy the example environment file and populate it with your specific secrets:
   ```bash
   cp .env.example .env
   ```
   *Note: Ensure all `CLERK`, `STRIPE`, and `DATABASE_URL` secrets are accurately set.*

3. **Bootstrap Local Services & Webhooks:**
   ```bash
   bash scripts/agent-services.sh
   ```

4. **Launch the Stack:**
   ```bash
   docker-compose up --build
   ```

5. **Access the application:**
   - **Frontend UI:** `http://localhost:7860`
   - **Compute API Docs:** `http://localhost:8000/docs`
   - **Auth API Docs:** `http://localhost:8001/docs`

## 🛠️ Local Development

### Running the Python Pipeline Tests

To run the Python unit tests and the end-to-end sync tag pipeline locally:

```bash
pip install -r requirements.txt
pytest tests/ -v
```

### CLI Audio Processing

You can process tracks manually using the python CLI directly:

**Run SyncTag Metageneration:**
```bash
python src/synctag.py input.wav -o output.json
```

**Run Stem Separation directly:**
```bash
python src/separate.py input.wav -o output_dir/
```

## 🔒 Security & Data

- All frontend routes require JWT verification validated through the Clerk JWKS cache.
- Internal service-to-service communication is secured via `.env` driven `INTERNAL_SECRET` tokens.
- Free-tier accounts have strictly enforced, atomic daily usage limits via the `auth` microservice.

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
