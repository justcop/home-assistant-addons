FROM ghcr.io/home-assistant/base:3.22
ARG BUILD_VERSION=0.6.16
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="addon" io.hass.arch="${BUILD_ARCH}"
ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1 AUDIOSHELF_VERSION=${BUILD_VERSION}
RUN apk add --no-cache python3 py3-pip && python3 -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY app /app/app
COPY build.json /app/build.json
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh
CMD ["/app/run.sh"]
