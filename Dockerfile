# BAW Bot — Docker image
# Fully isolated from Hermes: self-contained Python, no Hermes venv dependency.
FROM python:3.11-slim

# System deps (if any tools need them — playwrigth, pdf, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy BAW source
COPY . .

# Create baw user + home directory for ~/.baw/ volume mount
RUN useradd -m baw && \
    mkdir -p /home/baw/.baw && \
    chown -R baw:baw /app /home/baw/.baw

USER baw
ENV HOME=/home/baw

ENTRYPOINT ["python3", "-u", "baw-bot", "--platform", "telegram"]
CMD ["--debug"]
