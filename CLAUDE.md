# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository contains **CLAP (Contrastive Language-Audio Pretraining)** — a LAION open-source audio-text embedding model adapted from the CLIP architecture. The main package is `laion_clap`, located under `CLAP/src/laion_clap/`.

## Setup & Installation

```bash
cd CLAP
pip install -e .
```

For training from source (requires CUDA):
```bash
conda create -n clap python=3.10
conda activate clap
pip install torch torchaudio torchvision  # follow pytorch.org for CUDA version
pip install -r requirements.txt
```

## Common Commands

**Run the unit test / usage example:**
```bash
cd CLAP/src/laion_clap
python unit_test.py
```

**Run data/checkpoint tests:**
```bash
cd CLAP
pytest src/tests/
# or individually:
python src/tests/data_loader_test.py
python src/tests/check_ckpt.py
```

**Training (single GPU):**
```bash
CUDA_VISIBLE_DEVICES=0 python -m laion_clap.training.main <args>
```

**Training (multi-GPU with torchrun):**
```bash
torchrun --nproc_per_node=<N> -m laion_clap.training.main <args>
```
See `CLAP/experiment_scripts/` for full training/fine-tuning/eval script examples.

## Architecture

CLAP is a **dual-encoder contrastive learning** model:

```
Audio Input → Audio Encoder → Audio Embedding ─┐
                                                 ├─→ Contrastive Loss (ClipLoss)
Text Input  → Text Encoder  → Text Embedding  ─┘
```

### Public API (`hook.py`)
The single entry point for inference is `CLAP_Module` in `src/laion_clap/hook.py`:
- `CLAP_Module(enable_fusion, device, amodel, tmodel)` — wraps the full model
- `load_ckpt(ckpt, model_id)` — downloads or loads a pretrained checkpoint
- `get_audio_embedding_from_filelist(x, use_tensor)` — embed audio files
- `get_audio_embedding_from_data(x, use_tensor)` — embed numpy/tensor audio arrays
- `get_text_embedding(text, use_tensor)` — embed a list of strings

### Model Instantiation (`clap_module/factory.py`)
`create_model(amodel, tmodel, ...)` loads a JSON config from `clap_module/model_configs/` and instantiates the selected encoder pair. 24 JSON configs exist covering HTSAT variants (tiny/base/large), PANN models (Cnn6/10/14), and various text encoder configurations.

### Audio Encoders
- **HTSAT** (`htsat.py`) — Hierarchical Token-Semantic Audio Transformer; default encoder, used in all production checkpoints
- **PANN** (`pann_model.py`) — Pre-trained Audio Neural Networks (Cnn6, Cnn10, Cnn14)
- Both consume log-mel spectrograms via `torchlibrosa`; audio must be resampled to **48 kHz**

### Feature Fusion (`feature_fusion.py`)
When `enable_fusion=True`, the model uses `aff_2d` (Attention Feature Fusion) to handle variable-length audio beyond 10 seconds. Use `enable_fusion=False` for fixed ≤10s clips.

### Text Encoders
RoBERTa (default), BERT, or BART. Tokenization uses `RobertaTokenizer` with context length 77.

### Training Pipeline (`training/`)
- `main.py` — orchestrates training, checkpointing, W&B logging
- `train.py` — single epoch loop
- `data.py` — data loading for webdataset, CSV, H5, and toy formats; audio quantization helpers `int16_to_float32` / `float32_to_int16`
- `params.py` — all 100+ training hyperparameters via argparse
- `distributed.py` — optional Horovod multi-node wrapper

### Evaluation (`evaluate/`)
- `eval_zeroshot_classification.py` — zero-shot on ESC50/AudioCaps
- `eval_retrieval.py` — mAP@10, R@k for audio↔text retrieval
- `eval_linear_probe.py` — downstream linear probe tasks

## Audio Processing Conventions

- Sample rate: **48 kHz** (always resample before passing to the model)
- Clip length: 480,000 samples (~10 s); longer clips require `enable_fusion=True`
- Quantize float32 audio before embedding: `int16_to_float32(float32_to_int16(audio))`
- Mel-spectrogram: 64 bins, 50–14,000 Hz, window=1024, hop=480

## Pretrained Checkpoints

Hosted on HuggingFace at `lukewys/laion_clap`. Defaults loaded by `model.load_ckpt()`:
- General audio (≤10s): `630k-audioset-best.pt` (`HTSAT-tiny`, no fusion)
- Variable-length: `630k-audioset-fusion-best.pt` (`HTSAT-tiny`, fusion)
- Music/speech: use `HTSAT-base` with the `music_*` checkpoints

## Dataset Format

Training data uses **webdataset** (`.tar` shards). Each sample contains an audio file + JSON caption. See `CLAP/experiment_scripts/` for `--datasetpath` and `--remotedata` flags. Example ESC50 data in webdataset format is available on Google Drive (see README).

---

## Agent Workflows

### Adding Bugs — `.agent/workflows/addBugs.md`

Run this workflow **at the end of any debugging session** to capture bugs discovered during a build and persist them for future sessions.

**When to use:** After resolving build failures, runtime crashes, or non-obvious errors that took significant time to diagnose.

**What it does:**
1. Scans the conversation for error messages, stack traces, build failures, and applied fixes
2. Categorizes each bug into the appropriate `AGENT-CONTEXT/` file (e.g. `BackendApi.md`, `DatabaseStore.md`, `InfrastructureEnv.md`)
3. Appends structured entries with **Symptom / Root Cause / Remediation Plan**
4. Updates `AGENT-CONTEXT/AGENT-CONTEXT.md` index if a new category file is created

**Bug entry format:**
```markdown
### N. <Bug Title>
*   **Symptom:** <What error/message appeared>
*   **Root Cause:** <What was actually wrong>
*   **Remediation Plan:**
    1.  <Step 1>
    2.  <Step 2>
```

**Domain → file mapping:**

| Bug Domain | Target File |
|------------|-------------|
| Build, Docker, env vars, networking | `InfrastructureEnv.md` |
| React, UI, SSR, hydration | `FrontendClient.md` |
| Express, routes, auth, middleware | `BackendApi.md` |
| Async, timeouts, resource locks | `BackendExecution.md` |
| PostgreSQL, migrations, ORM | `DatabaseStore.md` |
| Planning loops, hallucination | `CognitionAndPlanning.md` |
| Context rot, memory issues | `MemoryState.md` |
| Injection, unauthorized actions | `SecurityGuardrails.md` |
| Deadlocks, recovery SOPs | `DevOpsUI.md` |

---

### Auditing a Build Plan — `.agent/workflows/auditPlan.md`

Run this workflow **before implementing any non-trivial build plan** to ground it against the actual codebase and pre-link known bug contexts.

**When to use:** After drafting a multi-step implementation plan and before writing any code.

**What it does:**
1. Loads navigation context from `AGENT-MAP.md`, `AGENT-CONTEXT/AGENT-CONTEXT.md`, and `.agent/skills/AGENT-SKILLS.md`
2. For every file the plan touches, verifies and adds exact line references using the format `[basename.py Lxx](file:///absolute/path#Lxx)`
3. Maps each build phase to the relevant `AGENT-CONTEXT/` bug file using the Triage table
4. Adds a `> [!WARNING]` block per phase pointing to the context files where errors are likely to arise
5. Prepends a **Step 0** to the plan with required reading and required skills tables
6. Validates a dependency DAG so each step declares what it depends on and what it enables

**Output checklist the audited plan must satisfy:**

| Category | Requirement |
|----------|-------------|
| Integration Refs | Every modified file has `[file.py Lxx](file:///path#Lxx)` links |
| Step 0 | Required Reading + Required Skills tables present |
| Bug Context | Each phase has a `> [!WARNING]` block with AGENT-CONTEXT links |
| Build Targets | Each step has `grep` verification commands |
| Dependency Chain | First step rooted, all steps ordered by dependency |
| Tests | Interface points have test specs with insert locations |
