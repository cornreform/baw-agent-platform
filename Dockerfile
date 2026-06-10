# BAW Bot — Docker image
# Fully isolated from Hermes: self-contained Python, no Hermes venv dependency.
FROM python:3.11-slim

# System deps (if any tools need them — playwright, pdf, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create baw user EARLY so it's cached (never changes, don't rebuild on code change)
RUN useradd -m baw && mkdir -p /home/baw/.baw

WORKDIR /app

# Install Python deps first (layer caching — only rebuilds when requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy BAW source LAST (this is the only layer that changes on most commits)
COPY . .

# Fix ownership after code copy
RUN chown -R baw:baw /app /home/baw/.baw

USER baw
ENV HOME=/home/baw

ENTRYPOINT ["python3", "-u", "baw-bot", "--platform", "telegram"]
CMD ["--debug"]
