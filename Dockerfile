FROM python:3.10-slim
WORKDIR /app
ENV PYTHONPATH=/app
ENV HF_HOME=/app/.cache/huggingface

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
RUN mkdir -p samples .cache/huggingface

# Pre-download model weights at build time (as root, writing to HF_HOME)
RUN python -c "from transformers import ClapModel, ClapFeatureExtractor; \
    ClapModel.from_pretrained('laion/clap-htsat-fused'); \
    ClapFeatureExtractor.from_pretrained('laion/clap-htsat-fused')"


RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app

USER app
EXPOSE 7860
CMD ["python", "-m", "src.app"]
