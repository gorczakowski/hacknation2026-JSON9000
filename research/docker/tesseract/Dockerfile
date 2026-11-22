# Test image: Tesseract 5 with Polish language data, used to compare OCR engines.
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-pol \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /data
