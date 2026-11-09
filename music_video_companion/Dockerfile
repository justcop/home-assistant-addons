FROM ghcr.io/home-assistant/base-python:3.15-alpine3.24-2026.11.0
ARG BUILD_VERSION=0.5.0
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="addon" io.hass.arch="${BUILD_ARCH}"
RUN apk add --no-cache ca-certificates tzdata
RUN python3 -m pip install --no-cache-dir websocket-client==1.9.2
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY app /app
CMD ["python3", "server.py"]
