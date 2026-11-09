FROM ghcr.io/home-assistant/base-python:3.15-alpine3.24-2026.11.0
ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1
RUN python3 -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
ARG BUILD_VERSION=0.6.27
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="addon" io.hass.arch="${BUILD_ARCH}"
ENV AUDIOSHELF_VERSION=${BUILD_VERSION}
WORKDIR /app
COPY app /app/app
COPY build.json /app/build.json
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh
CMD ["/app/run.sh"]
