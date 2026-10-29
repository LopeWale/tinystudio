# tinystudio application image: API, worker, and inference share it (the command selects the role).
#   docker build -t tinystudio:local .                                           CPU PyTorch
#   docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 -t tinystudio:cuda .
# Build the browser application with the official Node 24 image. Node and npm
# stay in this stage; the Python runtime receives only the compiled release.
FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/vendor/visual-truth/visual-truth-1.3.0.tgz ./vendor/visual-truth/visual-truth-1.3.0.tgz
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY tests/fixtures/lab_definition_v1.json /build/tests/fixtures/lab_definition_v1.json
RUN npm run build

FROM python:3.12-slim

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TINYSTUDIO_DATA_DIR=/data \
    TINYSTUDIO_CACHE_DIR=/tmp/tinystudio-cache \
    HF_HOME=/tmp/tinystudio-hub \
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
COPY scripts/build_frontend_hook.py scripts/check_react_assets.py ./scripts/
COPY --from=frontend-build /build/tinystudio/ui/dist ./tinystudio/ui/dist
RUN pip install ".[assistant]"
# The isolated Hatch hook validates before packaging; runtime parser deps now exist.
RUN python scripts/check_react_assets.py

USER studio
VOLUME ["/data"]
# ports come from TINYSTUDIO_PORT / TINYSTUDIO_INFERENCE_PORT at run time (see compose.yaml and .env.example)
CMD ["tinystudio", "serve"]
