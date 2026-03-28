ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.11-alpine3.18
# Use the correct architecture base image if not AMD64, but HA usually handles this via build.yaml
FROM $BUILD_FROM

ENV LANG C.UTF-8

# Install required system packages, including gcc to compile pyalsaaudio
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libportaudio2 \
    alsa-utils \
    libasound2-plugins \
    libasound2-dev \
    libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

# Install required Python packages
RUN pip install --no-cache-dir pyacoustid sounddevice numpy paho-mqtt pyalsaaudio

WORKDIR /usr/src/app

COPY run.sh /usr/src/app/run.sh
COPY vinyl_guardian.py /usr/src/app/vinyl_guardian.py

RUN chmod a+x /usr/src/app/run.sh

CMD [ "/usr/src/app/run.sh" ]