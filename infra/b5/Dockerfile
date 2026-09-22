FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/opt/rhetoriq/models
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates openssl && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./
ARG TORCH_VERSION=2.13.0+cpu
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}" \
    && pip install --no-cache-dir -r requirements.txt \
    && python -c "import torch; assert '+cpu' in torch.__version__ and not torch.cuda.is_available(), torch.__version__"
ARG MINILM_REVISION=c9745ed1d9f207416be6d2e6f8de32d1f16199bf
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', revision='${MINILM_REVISION}')"
ENV EMBEDDING_LOCAL_ONLY=true B5_MODEL_REVISION=${MINILM_REVISION} HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
RUN useradd --create-home --uid 10001 rhetoriq
COPY --chown=rhetoriq:rhetoriq backend/ ./
COPY --chown=rhetoriq:rhetoriq infra/b5/tls_init.py /opt/rhetoriq/tls_init.py
COPY --chown=rhetoriq:rhetoriq infra/flink/recorded-provider.json /opt/rhetoriq/recorded-provider.json
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "events.b5_worker"]
