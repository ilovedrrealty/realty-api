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
# The pinned base removes compiled caches after ensurepip. Reinstall its exact
# two bundled tool versions without compiling, rebuilding consistent RECORDs.
# Keep the base's unversioned pip alias outside the recorded console scripts.
RUN /usr/local/bin/python -I -B -c "import importlib.metadata as m; assert m.version('pip') == '24.0' and m.version('setuptools') == '79.0.1'" \
    && printf '%s\n' \
      'pip==24.0 --hash=sha256:ba0d021a166865d2265246961bec0152ff124de910c5cc39f1156ce3fa7c69dc' \
      'setuptools==79.0.1 --hash=sha256:e147c0549f27767ba362f9da434eab9c5dc0045d5304feb602a0af001089fc51' \
      > /opt/strict107/base-tools.txt \
    && ENSUREPIP_OPTIONS=install /usr/local/bin/python -I -B -m pip install \
      --retries 0 --timeout 30 --no-cache-dir --no-compile --no-deps \
      --only-binary=:all: --require-hashes --force-reinstall \
      --requirement /opt/strict107/base-tools.txt
RUN /usr/local/bin/python -m pip install --retries 0 --timeout 30 \
    --no-cache-dir --no-compile --requirement /opt/strict107/requirements.txt

WORKDIR /scratch
# The workflow supplies the sole runner command. No application entrypoint.
