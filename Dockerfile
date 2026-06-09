FROM python:3.12-slim

# openssh-client: remote host/file stats over ssh.  curl: container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Install with all extras (docker SDK + mttyd terminal).
RUN pip install --no-cache-dir ".[all]"

EXPOSE 8838
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
    CMD curl -fsS http://localhost:8838/healthz || exit 1

# Config is mounted at /app/pocketlab.yaml (see docker-compose.example.yml).
CMD ["pocketlab", "--host", "0.0.0.0", "--port", "8838"]
