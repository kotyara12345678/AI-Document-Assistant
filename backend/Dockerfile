FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/hf \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app app \
    && mkdir -p /data/uploads /data/hf && chown -R app:app /data

# ca-certificates + curl are needed to install the Ministry of Digital
# Development CA used by GigaChat TLS. fonts-dejavu-core provides a
# Cyrillic-capable Unicode TTF (DejaVuSans) used to render translated Russian
# text in edited PDFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch first (the default wheel bundles CUDA, which we don't need)
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r requirements-dev.txt

# Trust the Russian Trusted Root CA (НУЦ Минцифры) so GigaChat TLS is
# verified properly. Installed both into the OS store and the certifi bundle
# used by httpx/requests. (No verify=False anywhere.)
RUN curl -k -sSf "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt" \
        -o /usr/local/share/ca-certificates/russian_trusted_root_ca_pem.crt \
    && update-ca-certificates \
    && curl -k -sSf "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt" \
        -w "\n" >> "$(python -m certifi)"

COPY . .

RUN chmod +x entrypoint.sh \
    && chown -R app:app /app

USER app

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
