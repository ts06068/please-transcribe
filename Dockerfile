FROM node:22-bookworm-slim AS js-runtime
FROM python:3.12-slim-bookworm
COPY --from=js-runtime /usr/local/bin/node /usr/local/bin/node

ARG WITH_PDF=0
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browsers

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg tesseract-ocr tesseract-ocr-eng tesseract-ocr-kor tesseract-ocr-spa \
    fonts-noto-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install '.[media,cpu,url]' \
    && if [ "$WITH_PDF" = "1" ]; then \
         pip install '.[pdf]' && python -m playwright install --with-deps chromium; \
       fi

WORKDIR /work
ENTRYPOINT ["avtimeline"]
CMD ["--help"]
