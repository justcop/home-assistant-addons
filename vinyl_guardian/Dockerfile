ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.11-alpine3.18
FROM $BUILD_FROM

ENV LANG=C.UTF-8

# Added pulseaudio-utils to fix the 'pactl: not found' error
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
    chromaprint

# Install required Python packages
RUN python3 -m pip install --no-cache-dir --break-system-packages pyacoustid numpy paho-mqtt pyalsaaudio

WORKDIR /usr/src/app

COPY run.sh /usr/src/app/run.sh
COPY vinyl_guardian.py /usr/src/app/vinyl_guardian.py

RUN chmod a+x /usr/src/app/run.sh

CMD [ "/usr/src/app/run.sh" ]