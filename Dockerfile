# JSON9000 (Odwolania2json) - CPU-only image (no GPU needed). Python 3.13, MinerU (flash OCR, ONNX) + Tesseract (Polish).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MINERU_HOME=/opt/mineru HF_HOME=/opt/hf

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-pol libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# OCR models are baked into the image: nothing is downloaded at evaluation time.
RUN mineru-models-download MinerU-4_models_onnx

COPY odwolania2json ./odwolania2json
COPY config ./config
RUN python -m odwolania2json --check-config

# input/output are mounted by docker compose (see docker-compose.yml / RUN.md)
ENTRYPOINT ["python", "-m", "odwolania2json"]
CMD ["--input", "/app/input", "--output", "/app/output", "--evidence", "/app/evidence"]
