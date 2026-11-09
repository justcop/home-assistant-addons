FROM ghcr.io/home-assistant/base-python:3.15-alpine3.24-2026.11.0

ENV LANG=C.UTF-8

COPY requirements.txt /tmp/requirements.txt

RUN apk add --no-cache \
        alsa-lib \
        alsa-utils \
        alsa-plugins \
        alsa-plugins-pulse \
        pulseaudio-utils \
        ffmpeg \
    && apk add --no-cache --virtual .build-deps \
        gcc \
        musl-dev \
        alsa-lib-dev \
    && python3 -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && apk del .build-deps \
    && rm -f /tmp/requirements.txt

ARG BUILD_VERSION="dev"
ARG BUILD_ARCH="unknown"
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

WORKDIR /usr/src/app

COPY run.sh /usr/src/app/run.sh
COPY *.py /usr/src/app/

RUN chmod a+x /usr/src/app/run.sh \
    && python3 -m compileall -q /usr/src/app

CMD [ "/usr/src/app/run.sh" ]
