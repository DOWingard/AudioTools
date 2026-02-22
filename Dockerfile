FROM python:3.10-slim
WORKDIR /app
ENV PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 build-essential wget unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY m2d/ ./m2d/
RUN mkdir -p samples

# Download M2D-CLAP checkpoint (type B with BERT text encoder included)
RUN wget -q https://github.com/nttcslab/m2d/releases/download/v0.5.0/m2d_clap_vit_base-80x1001p16x16p16kpBpTI-2025.zip \
    && unzip -q m2d_clap_vit_base-80x1001p16x16p16kpBpTI-2025.zip \
    && rm m2d_clap_vit_base-80x1001p16x16p16kpBpTI-2025.zip

RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app

USER app
EXPOSE 7860
CMD ["python", "-m", "src.app"]
