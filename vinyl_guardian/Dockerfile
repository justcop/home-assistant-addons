ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.11-alpine3.18
FROM $BUILD_FROM

ENV LANG=C.UTF-8

RUN apk add --no-cache \
    gcc \
    musl-dev \
    python3-dev \
    py3-pip \
    alsa-lib-dev \
    alsa-utils \
    alsa-plugins \
    alsa-plugins-pulse \
    pulseaudio-utils \
    ffmpeg

RUN python3 -m pip install --no-cache-dir --break-system-packages numpy paho-mqtt pyalsaaudio requests shazamio pylast

WORKDIR /usr/src/app

COPY run.sh /usr/src/app/run.sh
# This tells Docker to grab every .py file in your folder
COPY *.py /usr/src/app/

RUN chmod a+x /usr/src/app/run.sh

CMD [ "/usr/src/app/run.sh" ]