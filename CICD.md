# CI/CD Reference — AudioTools (GraphSamples)

> **Repo:** https://github.com/DOWingard/AudioTools
> **Main branch:** `main`
> **Last audited:** 2026-03-02

---

## 1. Service Inventory

| Service | Dir | Port | Runtime | Purpose |
|---------|-----|------|---------|---------|
| **ui** | `ui/` | 7860 (nginx) | Node 20 / nginx | React + Vite SPA (served as static build) |
| **compute** | `api/` + `src/` | 8000 | Python 3.10 / FastAPI | Audio ML pipeline — tagging, separation, BPM, search |
| **auth** | `auth/` | 8001 | Python 3.11 / FastAPI | Clerk JWT validation, Stripe webhooks, user quota |
| **postgres** | `database/` | 5434 | PostgreSQL 17 | User & subscription records |
| **qdrant** | *(upstream image)* | 6333/6334 | Qdrant v1.9.0 | Vector DB for 768-dim M2D-CLAP embeddings |
| **minio** *(dev only)* | — | 9010/9011 | MinIO | Local S3 stand-in; replaced by Cloudflare R2 in prod |

### Internal routing (nginx → services)

```
Browser → nginx:7860
  /api/*   →  compute:8000
  /auth/*  →  auth:8001
```

---

## 2. Required GitHub Secrets

Set these under **Settings → Secrets and variables → Actions** in the GitHub repo before any workflow runs.

### Build-time (injected into Docker / Vite build)

| Secret name | Used by | Notes |
|-------------|---------|-------|
| `VITE_CLERK_PUBLISHABLE_KEY` | ui build | Baked into the Vite bundle at build time |
| `VITE_STRIPE_PUBLISHABLE_KEY` | ui build | Baked into the Vite bundle at build time |

### Runtime (injected into containers via env)

| Secret name | Service | Notes |
|-------------|---------|-------|
| `GEMINI_API_KEY` | compute | Google Generative AI for SyncTag stage 5 |
| `CLERK_SECRET_KEY` | auth | Server-side Clerk API key |
| `CLERK_WEBHOOK_SECRET` | auth | Svix signature validation |
| `STRIPE_SECRET_KEY` | auth | Stripe server key |
| `STRIPE_WEBHOOK_SECRET` | auth | Stripe webhook signature |
| `STRIPE_PRICE_ID_STANDARD` | auth | |
| `STRIPE_PRICE_ID_PREMIUM` | auth | |
| `POSTGRES_USER` | auth, postgres | |
| `POSTGRES_PASSWORD` | auth, postgres | |
| `POSTGRES_DB` | auth, postgres | |
| `R2_ACCOUNT_ID` | compute | Cloudflare R2 (prod object storage) |
| `R2_ACCESS_KEY_ID` | compute | |
| `R2_SECRET_ACCESS_KEY` | compute | |
| `R2_BUCKET_NAME` | compute | |
| `INTERNAL_SECRET` | compute, auth | Service-to-service purge key |
| `DEV_USER_ID` | compute | Only set in non-prod environments |

### Deployment (added once per target platform)

| Secret name | Notes |
|-------------|-------|
| `DOCKER_USERNAME` | Docker Hub registry push |
| `DOCKER_PASSWORD` | Docker Hub registry push |
| `DEPLOY_HOST` | SSH address of the VPS / cloud VM |
| `DEPLOY_USER` | SSH user (e.g. `deploy`) |
| `DEPLOY_SSH_KEY` | Private SSH key for remote deploy |

> **Security:** Remove `.env` from git tracking (`git rm --cached .env && echo '.env' >> .gitignore`). Add a `.env.example` with placeholder values for onboarding.

---

## 3. GitHub Actions Workflows

Create the following files under `.github/workflows/`.

---

### 3.1 CI — Pull Request checks (`.github/workflows/ci.yml`)

Runs on every PR targeting `main`. Blocks merge on failure.

```yaml
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  # ── Frontend ────────────────────────────────────────────────
  ui-lint-build:
    name: UI — lint & build
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: ui
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"
          cache-dependency-path: ui/package-lock.json

      - run: npm ci

      - name: Vite build (type-check + bundle)
        env:
          VITE_CLERK_PUBLISHABLE_KEY: ${{ secrets.VITE_CLERK_PUBLISHABLE_KEY }}
          VITE_STRIPE_PUBLISHABLE_KEY: ${{ secrets.VITE_STRIPE_PUBLISHABLE_KEY }}
        run: npm run build

  # ── Auth service ────────────────────────────────────────────
  auth-lint:
    name: Auth — lint & type-check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"
          cache-dependency-path: auth/requirements.txt

      - run: pip install -r auth/requirements.txt
      - run: pip install ruff
      - run: ruff check auth/

  auth-test:
    name: Auth — unit tests
    runs-on: ubuntu-latest
    needs: auth-lint
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"
          cache-dependency-path: auth/requirements.txt

      - run: pip install -r auth/requirements.txt
      - run: pip install pytest pytest-asyncio
      - run: pytest tests/test_auth_e2e.py -v
        env:
          DATABASE_URL: ""          # skips DB-dependent tests in CI

  # ── Compute service ─────────────────────────────────────────
  compute-lint:
    name: Compute — lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: "pip"

      - run: pip install ruff
      - run: ruff check api/ src/

  # ── Docker build smoke test ──────────────────────────────────
  docker-build:
    name: Docker — build all images
    runs-on: ubuntu-latest
    needs: [ui-lint-build, auth-lint, compute-lint]
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive

      - uses: docker/setup-buildx-action@v3

      - name: Build ui image
        uses: docker/build-push-action@v5
        with:
          context: .
          file: Dockerfile.ui
          push: false
          build-args: |
            VITE_CLERK_PUBLISHABLE_KEY=${{ secrets.VITE_CLERK_PUBLISHABLE_KEY }}
            VITE_STRIPE_PUBLISHABLE_KEY=${{ secrets.VITE_STRIPE_PUBLISHABLE_KEY }}

      - name: Build auth image
        uses: docker/build-push-action@v5
        with:
          context: .
          file: Dockerfile.auth
          push: false

      - name: Build compute image
        uses: docker/build-push-action@v5
        with:
          context: .
          file: Dockerfile.compute
          push: false
```

---

### 3.2 CD — Deploy on push to main (`.github/workflows/cd.yml`)

Runs after every merge to `main`. Builds, pushes images to Docker Hub, then SSHs into the server and does a rolling pull + restart.

```yaml
name: CD

on:
  push:
    branches: [main]

env:
  REGISTRY: docker.io
  IMAGE_PREFIX: ${{ secrets.DOCKER_USERNAME }}/audiotools

jobs:
  build-push:
    name: Build & push images
    runs-on: ubuntu-latest
    outputs:
      sha_short: ${{ steps.meta.outputs.sha_short }}

    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}

      - name: Compute short SHA
        id: meta
        run: echo "sha_short=$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"

      - name: Build & push ui
        uses: docker/build-push-action@v5
        with:
          context: .
          file: Dockerfile.ui
          push: true
          tags: |
            ${{ env.IMAGE_PREFIX }}-ui:${{ steps.meta.outputs.sha_short }}
            ${{ env.IMAGE_PREFIX }}-ui:latest
          build-args: |
            VITE_CLERK_PUBLISHABLE_KEY=${{ secrets.VITE_CLERK_PUBLISHABLE_KEY }}
            VITE_STRIPE_PUBLISHABLE_KEY=${{ secrets.VITE_STRIPE_PUBLISHABLE_KEY }}

      - name: Build & push auth
        uses: docker/build-push-action@v5
        with:
          context: .
          file: Dockerfile.auth
          push: true
          tags: |
            ${{ env.IMAGE_PREFIX }}-auth:${{ steps.meta.outputs.sha_short }}
            ${{ env.IMAGE_PREFIX }}-auth:latest

      - name: Build & push compute
        uses: docker/build-push-action@v5
        with:
          context: .
          file: Dockerfile.compute
          push: true
          tags: |
            ${{ env.IMAGE_PREFIX }}-compute:${{ steps.meta.outputs.sha_short }}
            ${{ env.IMAGE_PREFIX }}-compute:latest

  deploy:
    name: Deploy to server
    runs-on: ubuntu-latest
    needs: build-push
    environment: production

    steps:
      - uses: actions/checkout@v4

      - name: Write .env to server and restart stack
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: ${{ secrets.DEPLOY_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            set -e
            cd /opt/audiotools

            # Write runtime env file (never stored in git)
            cat > .env <<'ENVEOF'
            GEMINI_API_KEY=${{ secrets.GEMINI_API_KEY }}
            CLERK_SECRET_KEY=${{ secrets.CLERK_SECRET_KEY }}
            CLERK_WEBHOOK_SECRET=${{ secrets.CLERK_WEBHOOK_SECRET }}
            STRIPE_SECRET_KEY=${{ secrets.STRIPE_SECRET_KEY }}
            STRIPE_WEBHOOK_SECRET=${{ secrets.STRIPE_WEBHOOK_SECRET }}
            STRIPE_PRICE_ID_STANDARD=${{ secrets.STRIPE_PRICE_ID_STANDARD }}
            STRIPE_PRICE_ID_PREMIUM=${{ secrets.STRIPE_PRICE_ID_PREMIUM }}
            POSTGRES_USER=${{ secrets.POSTGRES_USER }}
            POSTGRES_PASSWORD=${{ secrets.POSTGRES_PASSWORD }}
            POSTGRES_DB=${{ secrets.POSTGRES_DB }}
            R2_ACCOUNT_ID=${{ secrets.R2_ACCOUNT_ID }}
            R2_ACCESS_KEY_ID=${{ secrets.R2_ACCESS_KEY_ID }}
            R2_SECRET_ACCESS_KEY=${{ secrets.R2_SECRET_ACCESS_KEY }}
            R2_BUCKET_NAME=${{ secrets.R2_BUCKET_NAME }}
            INTERNAL_SECRET=${{ secrets.INTERNAL_SECRET }}
            APP_URL=https://your-domain.com
            ENVEOF

            # Pull latest images and restart (zero-downtime per service)
            docker compose pull ui auth compute
            docker compose up -d --no-deps ui auth compute

            # Clean up dangling images
            docker image prune -f
```

> Replace `/opt/audiotools` with the actual deploy directory on your server and `APP_URL` with your production domain.

---

### 3.3 DB Migrations (`.github/workflows/migrate.yml`)

Run manually via **workflow_dispatch** or as a deploy step after schema changes.

```yaml
name: DB Migrate

on:
  workflow_dispatch:

jobs:
  migrate:
    runs-on: ubuntu-latest
    environment: production

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - run: pip install asyncpg

      - name: Apply init.sql
        env:
          DATABASE_URL: postgres://${{ secrets.POSTGRES_USER }}:${{ secrets.POSTGRES_PASSWORD }}@${{ secrets.DEPLOY_HOST }}:5434/${{ secrets.POSTGRES_DB }}
        run: |
          python - <<'EOF'
          import asyncio, asyncpg, os
          async def main():
              sql = open("database/init.sql").read()
              conn = await asyncpg.connect(os.environ["DATABASE_URL"])
              await conn.execute(sql)
              await conn.close()
          asyncio.run(main())
          EOF
```

---

## 4. Branch & Merge Strategy

```
feature/* ──► unified-ui ──► main
                              │
                              └── CD workflow fires → Docker Hub push → server deploy
```

- **PRs require CI green** before merge to `main`.
- Tag releases with `vX.Y.Z`; the CD workflow can optionally push that tag as a Docker image tag.
- `unified-ui` is the current integration branch — merge it to `main` when stable.

---

## 5. Deployment Targets

### Option A — Single VPS (current simplest path)

Run the full `docker-compose.yml` on a single machine. The CD workflow SSHs in and does `docker compose pull && up`.

**Recommended spec (minimum):** 4 vCPU, 16 GB RAM (Demucs + ML inference is memory-hungry).

**Server setup (one-time):**
```bash
# On server
mkdir -p /opt/audiotools
git clone https://github.com/DOWingard/AudioTools /opt/audiotools --recurse-submodules
# Add SSH key from GitHub Actions runner to ~/.ssh/authorized_keys
```

### Option B — Managed container platform

If you want to avoid managing the host OS:

| Service | Suggested platform | Notes |
|---------|-------------------|-------|
| ui (nginx static) | **Cloudflare Pages** or **Vercel** | Free tier; no Docker needed |
| compute (ML heavy) | **Railway** or **Fly.io** | Needs ≥4 GB RAM instance; GPU optional |
| auth (lightweight) | **Railway** or **Render** | Free tier works |
| postgres | **Neon** or **Railway Postgres** | Managed PG 17 |
| qdrant | **Qdrant Cloud** | Managed cluster; free tier for dev |
| object storage | **Cloudflare R2** | Already configured in .env |

> The compute service requires the M2D-CLAP checkpoint (~800MB). If deploying to a managed platform, download it at container startup via the existing Dockerfile mechanism rather than baking it into the image.

---

## 6. Local Dev → CI Parity Checklist

Before pushing a PR, verify locally:

```bash
# UI
cd ui && npm ci && npm run build

# Auth
pip install -r auth/requirements.txt ruff
ruff check auth/
pytest tests/test_auth_e2e.py -v

# Compute
ruff check api/ src/

# Full stack smoke test
docker compose up --build
```

---

## 7. Known Issues to Resolve Before First Pipeline Run

1. **`.env` in git** — `git rm --cached .env`, add to `.gitignore`, create `.env.example`.
2. **Git submodules** — CI checkouts must use `submodules: recursive` (already in workflows above) because `m2d/`, `demucs/`, and `LARS/` are submodules.
3. **Compute image size** — PyTorch + Demucs + M2D checkpoint make the image ~6–8 GB. Use multi-stage builds and cache `~/.cache/pip` in the Docker layer to keep CI fast.
4. **Chunk size warnings** — Vite warns on `vendor_react` chunk. Already mitigated by `chunkSizeWarningLimit: 600` in `vite.config.js`; treat as non-blocking in CI.
5. **DB-dependent tests in CI** — `test_auth_e2e.py` requires a live Postgres. Either spin up a service container in the workflow or skip with `pytest -m "not db"` until you add service containers.
