# SOURCE CANDIDATE ONLY. A real reviewed official sha256 digest is required.
ARG PYTHON_DIGEST
FROM docker.io/library/python:3.11-slim@${PYTHON_DIGEST}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Online dependency preparation happens before the isolated test namespace.
# No application source, account configuration or provider credentials are copied.
RUN apt-get -o Acquire::Retries=0 update \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/strict107/requirements.txt
RUN /usr/local/bin/python -m pip install --retries 0 --timeout 30 \
    --no-cache-dir --no-compile --requirement /opt/strict107/requirements.txt

WORKDIR /scratch
# The workflow supplies the sole runner command. No application entrypoint.
