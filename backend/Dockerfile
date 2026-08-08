FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/hf \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app app \
    && mkdir -p /data/uploads /data/hf && chown -R app:app /data

# CPU-only torch first (the default wheel bundles CUDA, which we don't need)
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r requirements-dev.txt

COPY . .

RUN chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
