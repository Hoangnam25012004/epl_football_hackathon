# The Hugging Face Space image (docs/day7-design.md section 2.2): the main image plus Ollama on
# the CPU and its model, run as uid 1000 as Spaces require. Synthetic data only.
#
#   docker build -t pundit-copilot .
#   docker build -f deploy/space/Dockerfile -t pundit-copilot-space .
#   docker run --cpus 2 -p 8000:8000 pundit-copilot-space
#
# Built from the repository root. BASE is the main image (space.yml passes its digest).

ARG BASE=pundit-copilot

FROM debian:bookworm-slim AS ollama
ARG OLLAMA_VERSION=0.40.2
ARG OLLAMA_SHA256=726bee78706c281b0eeef00746efe51a044d71c592c3f0b195820707f31fdf04
ARG LLM_MODEL=qwen2.5:1.5b
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl zstd \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
# the release ships the GPU runners too: only the CPU libraries at the top of lib/ollama are kept
RUN curl -fsSLo ollama.tar.zst \
      "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-amd64.tar.zst" \
 && echo "${OLLAMA_SHA256}  ollama.tar.zst" | sha256sum -c - \
 && mkdir -p /opt/ollama \
 && zstd -dc ollama.tar.zst | tar -x -C /opt/ollama \
 && rm ollama.tar.zst \
 && find /opt/ollama/lib/ollama -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
ENV OLLAMA_MODELS=/opt/ollama/models
RUN /opt/ollama/bin/ollama serve & \
    for i in $(seq 1 60); do /opt/ollama/bin/ollama list >/dev/null 2>&1 && break; sleep 1; done; \
    /opt/ollama/bin/ollama pull "${LLM_MODEL}" && kill $!

FROM ${BASE}
USER root
COPY --from=ollama /opt/ollama/bin/ollama /usr/local/bin/ollama
COPY --from=ollama /opt/ollama/lib/ollama /usr/local/lib/ollama
# uid 1000 owns the models: Ollama migrates their manifests on its first start
COPY --from=ollama --chown=1000:1000 /opt/ollama/models /opt/ollama/models
COPY deploy/space/start.sh deploy/space/env.space deploy/space/warm_model.py /app/space/
# Spaces run the container as uid 1000: it owns every directory the app writes to
RUN useradd --create-home --uid 1000 user \
 && chown -R user /app/backend/.data \
 && chmod 0755 /app/space/start.sh
ARG LLM_MODEL=qwen2.5:1.5b
ARG PUNDIT_BUILD=""
ENV HOME=/home/user \
    LLM_MODEL=${LLM_MODEL} \
    PUNDIT_BUILD=${PUNDIT_BUILD} \
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODELS=/opt/ollama/models \
    OLLAMA_KEEP_ALIVE=-1 \
    OLLAMA_NUM_PARALLEL=1 \
    OLLAMA_MAX_LOADED_MODELS=1
USER user
CMD ["/app/space/start.sh"]
