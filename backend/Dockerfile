FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
ARG TORCH_VERSION=2.13.0+cpu
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}" \
    && pip install --no-cache-dir -r requirements.txt \
    && python -c "import torch; assert '+cpu' in torch.__version__ and not torch.cuda.is_available(), torch.__version__"

RUN useradd --create-home --uid 10001 rhetoriq
COPY --chown=rhetoriq:rhetoriq . ./

USER 10001:10001

EXPOSE 8000

CMD ["sh", "-c", "python -m migrations && python -m uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
