# BAW Bot — Docker image
# Optimized layer caching: FROM → apt → useradd → pip → COPY --chown
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# useradd cached (never changes)
RUN useradd -m baw && mkdir -p /home/baw/.baw && chown -R baw:baw /home/baw/.baw

WORKDIR /app

# pip layer cached (only rebuilds when requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code copy with inline chown (single layer, no trailing chown -R)
COPY --chown=baw:baw . .

USER baw
ENV HOME=/home/baw
# Default log level: INFO. Override with BAW_LOG_LEVEL=DEBUG for verbose.
ENV BAW_LOG_LEVEL=INFO

ENTRYPOINT ["python3", "-u", "baw-bot", "--platform", "telegram"]
