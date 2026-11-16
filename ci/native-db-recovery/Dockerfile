FROM docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409
COPY requirements.txt /opt/fixture-requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl python3-venv \
    && python3 -m venv /opt/fixture-venv \
    && /opt/fixture-venv/bin/pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: --require-hashes -r /opt/fixture-requirements.txt \
    && rm -rf /var/lib/apt/lists/*
