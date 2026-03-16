FROM python:3.11-slim

WORKDIR /app

# Install curl + ca-certs for the kraken-cli download
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Download kraken-cli binary directly into /usr/local/bin (no cargo/rust needed)
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then TRIPLE="x86_64-unknown-linux-musl"; \
    elif [ "$ARCH" = "aarch64" ]; then TRIPLE="aarch64-unknown-linux-musl"; \
    else echo "Unsupported arch: $ARCH" && exit 1; fi && \
    curl -fsSL "https://github.com/krakenfx/kraken-cli/releases/latest/download/kraken-${TRIPLE}.tar.gz" \
      | tar xz -C /usr/local/bin && \
    chmod +x /usr/local/bin/kraken && \
    kraken --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "uvicorn ui_server:app --host 0.0.0.0 --port ${PORT:-8080}"]
