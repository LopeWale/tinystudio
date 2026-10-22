# tinystudio application image: API, worker, and inference share it (the command selects the role).
#   docker build -t tinystudio:local .                                           CPU PyTorch
#   docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 -t tinystudio:cuda .
FROM python:3.12-slim

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TINYSTUDIO_DATA_DIR=/data \
    HF_HOME=/data/hub \
    TOKENIZERS_PARALLELISM=false

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 studio \
 && mkdir -p /data && chown studio:studio /data

WORKDIR /app
# torch first, from the index that matches the target hardware, so pip does not pull the CUDA build from PyPI on CPU hosts
RUN pip install --index-url ${TORCH_INDEX_URL} torch
COPY pyproject.toml README.md ./
COPY tinystudio ./tinystudio
RUN pip install ".[assistant]"

USER studio
VOLUME ["/data"]
EXPOSE 8000 8001
CMD ["tinystudio", "serve", "--host", "0.0.0.0", "--port", "8000"]
