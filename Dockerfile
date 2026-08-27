# =========================
# 1. Builder Stage
# =========================
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc \
&& rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m spacy download en_core_web_sm


# =========================
# 2. Runtime Stage
# =========================
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
&& rm -rf /var/lib/apt/lists/*

# Copy Python packages and Presidio spaCy NLP model from builder stage
COPY --from=builder /usr/local /usr/local

# Copy app source code
COPY . .

# Entrypoint setup
COPY start_combined.sh /app/start_combined.sh
RUN chmod +x /app/start_combined.sh

EXPOSE 4020 

ENTRYPOINT ["/bin/bash", "/app/start_combined.sh"]