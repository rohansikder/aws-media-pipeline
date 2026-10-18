# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    AWS_DEFAULT_REGION=eu-west-1

# Ordinary builds need no secret. Managed networks can mount a combined CA bundle
# for this RUN only; the session certificate is never added to an image layer:
# docker build --secret id=proxy_ca,src=/path/to/ca.pem -f docker/worker.Dockerfile .
RUN --mount=type=secret,id=proxy_ca,target=/run/secrets/proxy_ca,required=false \
    if [ -s /run/secrets/proxy_ca ]; then \
      export PIP_CERT=/run/secrets/proxy_ca; \
    fi \
    && apt-get -o Acquire::https::CaInfo="$PIP_CERT" update \
    && apt-get -o Acquire::https::CaInfo="$PIP_CERT" install -y --no-install-recommends ffmpeg ca-certificates \
    && python -m pip install --no-cache-dir boto3==1.40.61 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin worker

WORKDIR /app
COPY --chown=10001:10001 services/pipeline/ /app/pipeline/
USER worker
ENTRYPOINT ["python", "-m", "pipeline.worker"]
