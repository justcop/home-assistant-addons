FROM python:3.11-slim-bullseye

WORKDIR /usr/src/app

# Install system dependencies required for audio processing
# Changed chromaprint-fpcalc to libchromaprint-tools for Debian compatibility
RUN apt-get update && apt-get install -y \
    libportaudio2 \
    alsa-utils \
    libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

# Install required Python packages
RUN pip install --no-cache-dir pyacoustid sounddevice numpy paho-mqtt

COPY run.sh /usr/src/app/run.sh
COPY vinyl_guardian.py /usr/src/app/vinyl_guardian.py

RUN chmod a+x /usr/src/app/run.sh

CMD [ "/usr/src/app/run.sh" ]
