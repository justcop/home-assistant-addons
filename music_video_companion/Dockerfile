FROM ghcr.io/home-assistant/base:3.22
ARG BUILD_VERSION=0.4.0
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="addon" io.hass.arch="${BUILD_ARCH}"
RUN apk add --no-cache python3 py3-websocket-client ca-certificates tzdata
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY app /app
CMD ["python3", "server.py"]
