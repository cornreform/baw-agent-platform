# BAW Bot — Docker image
# Optimized layer caching: FROM → apt → useradd → pip → COPY --chown
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# useradd cached (never changes)
RUN useradd -m baw && \
    mkdir -p /home/baw/.baw /home/baw/.local && \
    chown -R baw:baw /home/baw/

WORKDIR /app

# pip layer cached (only rebuilds when this line changes)
RUN pip install --no-cache-dir \
    docx2txt \
    httpx \
    pyyaml \
    pptx2md \
    python-pptx \
    python-docx \
    schedule \
    html2text \
    websocket-client \
    croniter \
    requests \
    beautifulsoup4

# Code copy with inline chown (single layer, no trailing chown -R)
COPY --chown=baw:baw . .
# Copy schedule.yaml to BAW data directory so cron tasks are pre-configured
COPY --chown=baw:baw schedule.yaml /home/baw/.baw/schedule.yaml

# Create baw CLI wrapper inside the container
RUN mkdir -p /home/baw/.local/bin && \
    echo '#!/usr/bin/env bash' > /home/baw/.local/bin/baw && \
    echo 'set -e' >> /home/baw/.local/bin/baw && \
    echo 'exec python3 -m cli.main "$@"' >> /home/baw/.local/bin/baw && \
    chmod +x /home/baw/.local/bin/baw && \
    chown baw:baw /home/baw/.local/bin/baw

USER baw
ENV HOME=/home/baw
ENV PATH=/home/baw/.local/bin:/home/baw/npm/node_modules/.bin:$PATH
# Default log level: INFO. Override with BAW_LOG_LEVEL=DEBUG for verbose.
ENV BAW_LOG_LEVEL=INFO

ENTRYPOINT ["python3", "-u", "baw-bot", "--platform", "telegram"]
