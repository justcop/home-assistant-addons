ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.11-alpine3.18
FROM $BUILD_FROM

ENV LANG=C.UTF-8

# Install Alpine system packages (apk instead of apt-get)
# Includes gcc, musl, and alsa headers to compile pyalsaaudio
# chromaprint provides the fpcalc binary needed by AcoustID
RUN apk add --no-cache \
    gcc \
    musl-dev \
    python3-dev \
    alsa-lib-dev \
    alsa-utils \
    alsa-plugins \
    alsa-plugins-pulse \
    chromaprint

# Install required Python packages (sounddevice removed)
RUN pip install --no-cache-dir pyacoustid numpy paho-mqtt pyalsaaudio

WORKDIR /usr/src/app

COPY run.sh /usr/src/app/run.sh
COPY vinyl_guardian.py /usr/src/app/vinyl_guardian.py

RUN chmod a+x /usr/src/app/run.sh

CMD [ "/usr/src/app/run.sh" ]