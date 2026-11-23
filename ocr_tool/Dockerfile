# https://developers.home-assistant.io/docs/apps/configuration#app-dockerfile
# base-python is now published as a single multi-arch (amd64/aarch64) manifest,
# so there is no need to select a per-arch image via build.yaml/BUILD_FROM anymore.
FROM ghcr.io/home-assistant/base-python:3.13-alpine3.22

ARG BUILD_ARCH
ARG BUILD_VERSION="1.1.0"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN apk add --no-cache \
    tesseract-ocr \
    tesseract-ocr-data-eng \
    libstdc++

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}" \
    org.opencontainers.image.title="OCR Tool" \
    org.opencontainers.image.description="Local OCR for screenshots and PDFs, accessible through Home Assistant." \
    org.opencontainers.image.licenses="MIT"

COPY app.py /app/app.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

WORKDIR /app
CMD ["/run.sh"]
