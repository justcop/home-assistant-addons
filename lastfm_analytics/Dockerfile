FROM ghcr.io/home-assistant/base-python:3.15-alpine3.24-2026.11.0
ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1
RUN apk add --no-cache tzdata && python3 -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
ARG BUILD_VERSION=0.1.0
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="addon" io.hass.arch="${BUILD_ARCH}"
WORKDIR /app
COPY analytics /app/analytics
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh
CMD ["/app/run.sh"]
