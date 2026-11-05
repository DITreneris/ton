FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TON_GRAM_DATA_DIR=/data \
    TON_GRAM_HOST=0.0.0.0

WORKDIR /app
COPY pyproject.toml README.md CHANGELOG.md ROADMAP.md ./
COPY src ./src
COPY docs ./docs
# Measured gas_profile.json is package data under src/ton_gram/data/.
# Do not COPY artifacts/: .dockerignore excludes that directory.
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data

USER appuser
# Do not declare VOLUME here: Railway rejects it. Mount /data with
# `docker run -v gram-paper:/data` or a Railway volume at /data.
EXPOSE 43177

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; p=os.environ.get('TON_GRAM_PORT') or os.environ.get('PORT') or '43177'; urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=4).read()"

CMD ["python", "-m", "ton_gram", "worker"]
